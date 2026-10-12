"""Private subpackage: shared prompt fragment modules.

Most modules here export one SCREAMING_SNAKE_CASE constant.
`architecture_overview` exports a renderer, because the layout it
used to describe is manifest-overridable and freezing it into a string would
contradict the workspace's own configuration. `type_list` exports two
renderers that take the proposal pool's types, so no type name or gloss is
written down. `page_categories` renders the schema set's category table.
Every module carries a one-line provenance header naming where the text came from; a
source that no longer exists is marked *adopted, source retired*.
"""
