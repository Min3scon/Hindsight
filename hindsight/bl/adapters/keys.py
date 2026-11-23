"""Data adapters: record keys, path prefixes and the caches shared between the kinds (see ADAPTERS.md)."""
from __future__ import annotations




                             # waits to put in (the object's state holds what Blender has meanwhile)
COLL_NAMES: dict = {}        # D55: collection id -> its name when last seen in a scene (for messages)
_RECREATED: set = set()      # D55: collections Hindsight made again (removed when taken out and empty)
ANIMATED = "~animated"    # stands in for a value an animation or driver sets (never written back)


# The parts of an object's record the depsgraph reports changes of (its settings, pose, bones,
# animation, node groups, particle settings, its data's settings, shape and animation): kept per
# object until the object or something it depends on changes (datarec.on_update calls
# value_changed), an operator ends with it selected, or Hindsight writes it.
_EXT_CACHE: dict = {}        # object session_uid -> values
_EXT_DEPS: dict = {}         # session_uid of a datablock -> session_uids of the objects using it


# ----------------------------------------------------------------------------- materials (D112)
MAT_PREFIX = "@mat:"
MATP_PREFIX = "@matp:"


# D112: a material's record is read again only after the material or its node tree changed (the
# depsgraph says so, datarec.on_update calls material_changed); reading every material's node tree on
# every operator was three times the cost of the whole operator at 500 objects.
_MAT_CACHE: dict = {}        # material session_uid -> record


# ----------------------------------------------------------------------------- node groups (D126)
NG_PREFIX = "@ng:"
NGA_PREFIX = "@nga:"       # a node group's own animation (keys on its inputs, drivers, NLA): "@nga:<group id>" -> anim_of


# D117 style: an object's material part (slots, owned material records) is kept until the depsgraph
# reports the object changed (a slot or its material changing tags it) or an operator ends with it
# selected; what the depsgraph doesn't see is read every time (material_props)
_OBMAT_CACHE: dict = {}      # object session_uid -> (slots, {MAT_PREFIX + id: record})


# ----------------------------------------------------------------------------- bones (D129)
BONES = "bones"
_BONES_CACHE: dict = {}        # armature session_uid -> recorded bones


# ----------------------------------------------------------------------------- pose (D119)
POSE_KEY = "pose"


# ----------------------------------------------------------------------------- ID properties (D114)
# Custom properties and the data add-ons keep on a datablock (their PointerProperty and
# CollectionProperty groups and other registered properties: ID properties underneath). Blender 4 keeps
# both in the datablock's own ID properties; Blender 5 keeps an add-on's in a separate system group.
# They are recorded as they are stored, in one read each, so any add-on's data is recorded without
# knowing the add-on, and putting them back gives exactly what was stored (a value that wasn't set is
# taken out again, so it reads as the add-on's default).
IDPROPS_KEY = "idprops"
SYSPROPS_KEY = "sysprops"
DATA_PREFIX = "data."
SETTINGS_KEY = "settings"
DATA_VALUES = "values"
_VAL_CACHE: dict = {}          # session_uid -> recorded settings


# ----------------------------------------------------------------------------- animation (D120)
# Which action an object plays (and its slot on Blender 4.4+), how (blend, influence, extrapolation),
# and the action itself: every F-curve of the object's slot with all its keyframes, read in bulk. An
# action is recorded with the first recorded object by name that plays it with that slot, and kept
# until the depsgraph says it changed (keyframe edits tell it) or an operator ends with a user of it
# selected.
ANIM_KEY = "anim"
ACT_PREFIX = "@act:"
_ACT_CACHE: dict = {}          # action session_uid -> {slot identifier: record}


# ----------------------------------------------------------------------------- geometry of other types (D121)
# Curve and surface splines with their points, lattice points and metaball elements: editable in
# Object Mode, so recorded like settings (kept until the depsgraph reports the data changed). Changes
# made in Edit Mode reach the data when Edit Mode ends, and are recorded then.
GEOM = "geom"
_GEOM_CACHE: dict = {}         # data session_uid -> recorded geometry


SK_PREFIX = "sk."               # the shape keys' own (their animation)
MATA_PREFIX = "@mata:"          # a recorded material's node tree animation: "@mata:<material id>|<key>"
_NO_SLOTS: dict = {}            # active index -> [index], the slots value of an object without slots


REF_KEY = "__hindsight_ref__"   # a custom property that points to a datablock, recorded by its id


# ----------------------------------------------------------------------------- particle settings (D137)
PART_PREFIX = "@part:"


# ----------------------------------------------------------------------------- scene settings (D116)
WORLD_PREFIX = "world:"


WORLDP_PREFIX = "worldp:"
COMP_TREE = "comp_tree"


# ----------------------------------------------------------------------------- data no object uses (D151)
TEXT_PREFIX = "@tx:"       # a text: {"body": what it says, "props": its settings}
LMAT_PREFIX = "@lm:"       # a material no object uses (kept with a fake user, a library): material_record
LWORLD_PREFIX = "@lw:"     # a world no scene uses: material_record
LPROPS_PREFIX = "@lp:"     # custom properties and add-on data of a datablock nothing else records: id_props
LOOSE_PREFIXES = (TEXT_PREFIX, LMAT_PREFIX, LWORLD_PREFIX, LPROPS_PREFIX)
# D152: what it takes to make one of these (and the node groups of D133) again, and which of them there were
META_PREFIX = "@lx:"       # [kind "tx" | "lm" | "lw" | "ng", name, fake user, node tree bl_idname]
INV_LOOSE = "@inv:loose"   # the ids of the texts, unused materials and worlds there are (sorted); [] when none
INV_GROUPS = "@inv:groups"  # the same for the node groups no object uses (D133)
# D154: file-level datablocks (images, palettes, actions nothing uses...) as .blend snapshots, made again from them
SNAP_PREFIX = "@ls:"       # [collection, DNA id, the snapshot's blocks but its DNA (zstd, base64), name, fake user]
DNA_PREFIX = "@dna:"       # DNA id -> the DNA block those snapshots were written with (zstd, base64): kept once


# ----------------------------------------------------------------------------- collection settings (D127)
COLL_SETTINGS = "coll_settings"
