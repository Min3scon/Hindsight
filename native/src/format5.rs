//! Format 5's parts (dev/FORMAT5_LAYOUT.md, dev/FORMAT5_DAMAGE.md): the bytes of the index part and of the
//! step parts that hold the history inside the .blend. The JSON in them (the index part's registry, a step
//! part's metas) stays bytes here: the Python side writes and reads it.
//!
//!   index part = b"HSI5" | u32 JSON length | JSON | u32 CRC-32 of everything before it
//!   step part  = step table | u32 CRC-32 of the step table | metas | u32 CRC-32 of the metas | steps
//!   step table = b"HSP5" | u64 first step's number | u32 number of steps n | u32 metas length
//!                | n x (u64 step length, u32 step CRC-32)
//!
//! Little-endian. The steps lie back to back from `begin` (24 + 12 n + metas length + 4, also kept in the index
//! part) to the part's end. A step table whose CRC-32 or lengths don't match the part is damaged as a whole
//! (#69 adds the search by prefix); damaged metas leave the steps readable.

use super::*;

const INDEX_MAGIC: &[u8; 4] = b"HSI5";
const PART_MAGIC: &[u8; 4] = b"HSP5";
const TABLE_FIXED: usize = 20;
const ROW: usize = 12;

fn u32_le(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(b[o..o + 4].try_into().unwrap())
}

fn u64_le(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(b[o..o + 8].try_into().unwrap())
}

fn crc(b: &[u8]) -> u32 {
    crc32fast::hash(b)
}

fn len32(n: usize, what: &str) -> PyResult<u32> {
    u32::try_from(n).map_err(|_| bad(&format!("format 5: {what} too long")))
}

/// The index part around its JSON.
#[pyfunction]
fn index_build<'py>(py: Python<'py>, doc: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    guard(|| {
        let d = Buf::new(doc, false)?;
        let n = len32(d.len(), "index part")?;
        PyBytes::new_bound_with(py, d.len() + 12, |out| {
            out[..4].copy_from_slice(INDEX_MAGIC);
            out[4..8].copy_from_slice(&n.to_le_bytes());
            out[8..8 + d.len()].copy_from_slice(d.bytes());
            let c = crc(&out[..8 + d.len()]);
            out[8 + d.len()..].copy_from_slice(&c.to_le_bytes());
            Ok(())
        })
    })
}

/// The index part's JSON, or None when it is damaged, cut short or not an index part.
#[pyfunction]
fn index_read<'py>(py: Python<'py>, part: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyBytes>>> {
    guard(|| {
        let p = Buf::new(part, false)?;
        let b = p.bytes();
        if b.len() < 12 || &b[..4] != INDEX_MAGIC || u32_le(b, 4) as usize != b.len() - 12 {
            return Ok(None);
        }
        let end = b.len() - 4;
        if crc(&b[..end]) != u32_le(b, end) {
            return Ok(None);
        }
        Ok(Some(PyBytes::new_bound(py, &b[8..end])))
    })
}

/// A step part of `steps` (bytes-like, the history's steps from number `first` on), their CRC-32s and the
/// metas JSON: (part, where its steps begin, CRC-32 of the part). The steps are copied, never decoded.
#[pyfunction]
fn part_build<'py>(
    py: Python<'py>,
    first: u64,
    steps: &Bound<'py, PyList>,
    crcs: Vec<u32>,
    metas: &Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyBytes>, usize, u32)> {
    guard(|| {
        if crcs.len() != steps.len() {
            return Err(bad("format 5: one CRC-32 per step"));
        }
        let bufs = steps.iter().map(|s| Buf::new(&s, false)).collect::<PyResult<Vec<_>>>()?;
        let m = Buf::new(metas, false)?;
        let n = len32(bufs.len(), "step part")?;
        let ml = len32(m.len(), "metas")?;
        let table = TABLE_FIXED + ROW * bufs.len();
        let begin = table + 4 + m.len() + 4;
        let total = bufs.iter().try_fold(begin, |t, b| t.checked_add(b.len())).ok_or_else(|| bad("format 5: step part too long"))?;
        let mut whole = 0u32;
        let part = PyBytes::new_bound_with(py, total, |out| {
            out[..4].copy_from_slice(PART_MAGIC);
            out[4..12].copy_from_slice(&first.to_le_bytes());
            out[12..16].copy_from_slice(&n.to_le_bytes());
            out[16..20].copy_from_slice(&ml.to_le_bytes());
            let mut o = begin;
            for (i, (b, c)) in bufs.iter().zip(&crcs).enumerate() {
                let r = TABLE_FIXED + ROW * i;
                out[r..r + 8].copy_from_slice(&(b.len() as u64).to_le_bytes());
                out[r + 8..r + 12].copy_from_slice(&c.to_le_bytes());
                out[o..o + b.len()].copy_from_slice(b.bytes());
                o += b.len();
            }
            let tc = crc(&out[..table]);
            out[table..table + 4].copy_from_slice(&tc.to_le_bytes());
            out[table + 4..table + 4 + m.len()].copy_from_slice(m.bytes());
            let mc = crc(m.bytes());
            out[begin - 4..begin].copy_from_slice(&mc.to_le_bytes());
            whole = crc(out);
            Ok(())
        })?;
        Ok((part, begin, whole))
    })
}

/// A step part read back: (first step's number, offsets, lengths, CRC-32s of its steps, metas JSON or None
/// when the metas are damaged). CorruptBlob when the step table is damaged or doesn't fit the part.
#[pyfunction]
fn part_read<'py>(py: Python<'py>, part: &Bound<'py, PyAny>) -> PyResult<PyObject> {
    guard(|| {
        let p = Buf::new(part, false)?;
        let b = p.bytes();
        let damaged = || CorruptBlob::new_err("format 5: the step part's step table is damaged");
        if b.len() < TABLE_FIXED + 4 || &b[..4] != PART_MAGIC {
            return Err(damaged());
        }
        let (n, ml) = (u32_le(b, 12) as u128, u32_le(b, 16) as u128);
        let table = TABLE_FIXED as u128 + ROW as u128 * n;
        let begin = table + 4 + ml + 4;
        if begin > b.len() as u128 {
            return Err(damaged());
        }
        let (table, begin) = (table as usize, begin as usize);
        if crc(&b[..table]) != u32_le(b, table) {
            return Err(damaged());
        }
        let n = n as usize;
        let (mut offs, mut lens, mut crcs) = (Vec::with_capacity(n), Vec::with_capacity(n), Vec::with_capacity(n));
        let mut o = begin as u128;
        for i in 0..n {
            let r = TABLE_FIXED + ROW * i;
            let l = u64_le(b, r);
            offs.push(o as usize);
            lens.push(l);
            crcs.push(u32_le(b, r + 8));
            o += l as u128;
        }
        if o != b.len() as u128 {
            return Err(damaged());
        }
        let metas = &b[table + 4..begin - 4];
        let metas = (crc(metas) == u32_le(b, begin - 4)).then(|| PyBytes::new_bound(py, metas));
        Ok((u64_le(b, 4), offs, lens, crcs, metas).into_py(py))
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(index_build, m)?)?;
    m.add_function(wrap_pyfunction!(index_read, m)?)?;
    m.add_function(wrap_pyfunction!(part_build, m)?)?;
    m.add_function(wrap_pyfunction!(part_read, m)?)?;
    Ok(())
}
