"""Category-agnostic geometry operators over locally recomputed point sets.

These operators know nothing about tasks, object categories or graph anchors:
they take points and rays and return geometry or fail.  Failure is raised, never
downgraded to a value — callers own the status vocabulary they publish, because
only they know which artifact reason code a precondition maps to.  NumPy stays a
function-local import so importing the offline package remains cheap.
"""

from __future__ import annotations

import math


class OperatorError(ValueError):
    """A geometry precondition the caller must map onto its own status.

    ``reason`` is a stable snake_case code describing which precondition failed.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fit_plane(points):
    """Fit a plane to ``points`` by taking the smallest right singular vector.

    Returns ``(normal, centroid, rmse)``: a unit normal, the centroid the plane
    passes through, and the flatness RMSE of the signed point-to-plane
    distances.  The second singular value must stay above ``1e-8`` so the points
    actually span two dimensions; a line or a single cluster has no plane and
    raises instead of returning an arbitrary normal.  The normal sign is
    whatever the decomposition produced — callers disambiguate it themselves.
    """

    import numpy as np

    values = points.astype(np.float64)
    centroid = values.mean(axis=0)
    centered = values - centroid
    try:
        _, singular_values, right_vectors = np.linalg.svd(
            centered, full_matrices=False
        )
    except np.linalg.LinAlgError as error:
        raise OperatorError("plane_fit_failed") from error
    if len(singular_values) < 3 or singular_values[1] <= 1e-8:
        raise OperatorError("plane_points_are_degenerate")
    normal = right_vectors[-1]
    normal /= np.linalg.norm(normal)
    distances = centered @ normal
    rmse = float(np.sqrt(np.mean(distances * distances)))
    return normal, centroid, rmse


def intersect_ray_plane(direction, *, normal, plane_point):
    """Intersect the camera-origin ray along ``direction`` with a plane.

    ``direction`` need not be unit length: the intersection is scale invariant,
    so a raw pinhole direction ``[(u-cx)/fx, (v-cy)/fy, 1]`` is accepted as is.
    A ray nearly parallel to the plane and an intersection at or behind the
    camera origin are both refused rather than returned as a far-away or
    mirrored point.
    """

    import numpy as np

    ray = np.asarray(direction, dtype=np.float64)
    denominator = float(normal @ ray)
    if abs(denominator) <= 1e-8:
        raise OperatorError("ray_parallel_to_plane")
    scale = float((normal @ plane_point) / denominator)
    if not math.isfinite(scale) or scale <= 0.0:
        raise OperatorError("plane_intersection_behind_camera")
    return ray * scale


def fit_principal_axis(points) -> list[float]:
    """Return the dominant PCA axis of ``points`` as a unit vector.

    The axis is only reported when it is unambiguous: a second singular value
    within 80% of the first means the points have no single dominant direction,
    which raises instead of picking an arbitrary one.  The sign is fixed by
    making the largest-magnitude component positive so repeated calls on the
    same cloud agree.
    """

    import numpy as np

    if len(points) < 3:
        raise ValueError("object point cloud needs at least three points for PCA")
    values = points.astype(np.float64)
    centered = values - values.mean(axis=0)
    try:
        _, singular_values, right_vectors = np.linalg.svd(
            centered, full_matrices=False
        )
    except np.linalg.LinAlgError as error:
        raise ValueError("object PCA failed") from error
    if not len(singular_values) or singular_values[0] <= 1e-8:
        raise ValueError("object point cloud has no stable principal axis")
    if len(singular_values) > 1 and singular_values[1] / singular_values[0] > 0.8:
        raise ValueError("object principal axis is ambiguous")
    axis = right_vectors[0]
    axis /= np.linalg.norm(axis)
    dominant = int(np.argmax(np.abs(axis)))
    if axis[dominant] < 0.0:
        axis = -axis
    return [float(item) for item in axis]
