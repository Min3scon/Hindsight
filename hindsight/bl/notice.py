"""The small notice shared by the sidebar, the popover and the add-on preferences."""
ISSUES_URL = "https://github.com/Min3scon/Hindsight/issues/new/choose"
NOTE = "Back up your files, and tell me what breaks."


def draw_note(layout) -> None:
    """One line of text plus a 'Report a bug' button that opens the issue page."""
    row = layout.row(align=True)
    row.label(text=NOTE, icon='INFO')
    op = row.operator("wm.url_open", text="Report a bug", icon='URL')
    op.url = ISSUES_URL
