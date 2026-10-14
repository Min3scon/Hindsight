"""Small, unobtrusive beta notice shared by the sidebar and the add-on preferences."""
ISSUES_URL = "https://github.com/Min3scon/Hindsight/issues/new/choose"
NOTE = "Beta: please report bugs on GitHub"


def draw_header_tag(layout) -> None:
    """A small 'BETA' tag for a panel header."""
    layout.label(text="BETA")


def draw_note(layout) -> None:
    """One line of text plus a 'Report a bug' button that opens the issue page."""
    row = layout.row(align=True)
    row.label(text=NOTE, icon='INFO')
    op = row.operator("wm.url_open", text="Report a bug", icon='URL')
    op.url = ISSUES_URL
