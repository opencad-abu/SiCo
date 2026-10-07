"""Preconditioned conjugate gradients for bounded regularized normal systems."""

import math


def solve_positive_system(matrix, rhs):
    n = len(rhs)
    if not 1 <= n <= 2048 or len(matrix) != n or any(len(row) != n for row in matrix):
        raise ValueError("normal system exceeds solver bounds")
    if any(not math.isfinite(v) for row in matrix for v in row) or any(not math.isfinite(v) for v in rhs):
        raise ValueError("normal system contains nonfinite values")
    if any(matrix[i][i] <= 0 for i in range(n)):
        raise ValueError("normal matrix diagonal must be positive")
    scale = [math.sqrt(matrix[i][i]) for i in range(n)]
    rows = [[(j, v/scale[i]/scale[j]) for j, v in enumerate(row) if v]
            for i, row in enumerate(matrix)]
    b = [v/s for v, s in zip(rhs, scale)]
    x, r, p = [0.]*n, list(b), list(b)
    rr = sum(v*v for v in r)
    threshold = max(1e-26, rr*1e-22)
    if rr <= threshold:
        return x
    for _ in range(8*n):
        ap = [sum(v*p[j] for j, v in row) for row in rows]
        denominator = sum(a*b for a, b in zip(p, ap))
        if denominator <= 0:
            raise ValueError("normal system is not positive definite")
        alpha = rr/denominator
        x = [v+alpha*d for v, d in zip(x, p)]
        r = [v-alpha*d for v, d in zip(r, ap)]
        new_rr = sum(v*v for v in r)
        if new_rr <= threshold:
            return [v/s for v, s in zip(x, scale)]
        p = [v+(new_rr/rr)*d for v, d in zip(r, p)]
        rr = new_rr
    raise ValueError("normal system failed to converge within iteration budget")
