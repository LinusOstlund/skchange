"""Utility functions for change detection."""

import numpy as np
import pandas as pd


def changepoints_to_labels(changepoints: list, n) -> np.ndarray:
    """Convert a list of changepoints to a list of labels.

    Parameters
    ----------
    changepoints : list
        List of changepoint indices.
    n: int
        Sample size.

    Returns
    -------
    labels : np.ndarray
        1D array of labels: 0 for the first segment, 1 for the second, etc.
    """
    changepoints = [-1] + changepoints + [n - 1]
    labels = np.zeros(n)
    for i in range(len(changepoints) - 1):
        labels[changepoints[i] + 1 : changepoints[i + 1] + 1] = i
    return labels


def format_changepoint_output(
    fmt: str,
    labels: str,
    changepoints: list,
    X_index: pd.Index,
    scores: np.ndarray = None,
) -> pd.Series:
    """Format the predict method output of change detectors.

    Parameters
    ----------
    fmt : str
        Format of the output. Either "sparse" or "dense".
    labels : str
        Labels of the output. Either "indicator", "score" or "int_label".
    changepoints : list
        List of changepoint indices.
    X_index : pd.Index
        Index of the input data.
    scores : np.ndarray, optional (default=None)
        Array of scores.

    Returns
    -------
    pd.Series
        Either a sparse or dense pd.Series of boolean labels, integer labels or scores.
    """
    if fmt == "sparse" and labels in ["int_label", "indicator"]:
        out = pd.Series(changepoints, name="changepoints", dtype=int)
    elif fmt == "sparse" and labels == "score":
        out = pd.Series(
            scores[changepoints], index=changepoints, name="score", dtype=float
        )
    elif fmt == "dense" and labels == "int_label":
        out = changepoints_to_labels(changepoints, len(X_index))
        out = pd.Series(out, index=X_index, name="int_label", dtype=int)
    elif fmt == "dense" and labels == "indicator":
        out = pd.Series(False, index=X_index, name="indicator", dtype=bool)
        out.iloc[changepoints] = True
    elif fmt == "dense" and labels == "score":
        out = pd.Series(scores, index=X_index, name="score", dtype=float)
    return out
