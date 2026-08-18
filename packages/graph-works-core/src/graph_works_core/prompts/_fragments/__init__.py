"""Private subpackage: shared prompt fragment modules.

Each module here exports one SCREAMING_SNAKE_CASE constant — with two
exceptions. `architecture_overview` exports a renderer, because the layout it
used to describe is manifest-overridable and freezing it into a string would
contradict the workspace's own configuration. `lane_list` exports both: the
renderer takes the loaded `LaneSet` so no lane name is written down, and the
constant beside it is the gloss text, which the schemas do not carry. Every
module carries a one-line provenance header naming where the text came from; a
source that no longer exists is marked *adopted, source retired*.
"""
