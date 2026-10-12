"""Public work-mutation executor surface.

The durable implementation is workspace substrate because work, archive, and
orchestration are independent core verticals that all compose the same
transaction boundary.
"""

from gw_pipeline.transactions import MutationApplication, apply_mutation

__all__ = ["MutationApplication", "apply_mutation"]
