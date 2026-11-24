"""Personal models fitted from recorded activities (Phase 7, ADR-005, ADR-016).

Pure functions on plain data: no database, files, or network. The
application layer collects the samples; results are *fitted* assumptions
with sample counts and error ranges, never silent personal claims.
"""

from alpha.modelling.baseline import (
    EFFORT_SELECTIONS,
    ActivitySample,
    BaselineParams,
    EffortGroup,
    FitStatus,
    MultiplierFit,
    PaceVsModel,
    SampleResult,
    Validation,
    effort_groups,
    fit_speed_multiplier,
    pace_vs_models,
)
from alpha.modelling.split import SplitFit, SplitSampleResult, fit_split_speeds

__all__ = [
    "EFFORT_SELECTIONS",
    "ActivitySample",
    "BaselineParams",
    "EffortGroup",
    "FitStatus",
    "MultiplierFit",
    "PaceVsModel",
    "SampleResult",
    "SplitFit",
    "SplitSampleResult",
    "Validation",
    "effort_groups",
    "fit_speed_multiplier",
    "fit_split_speeds",
    "pace_vs_models",
]
