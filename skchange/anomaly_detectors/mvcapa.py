"""The subset multivariate collective and point anomalies (MVCAPA) algorithm."""

__author__ = ["mtveten"]
__all__ = ["Mvcapa"]

from typing import Callable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from numba import njit
from scipy.stats import chi2
from sktime.annotation.base import BaseSeriesAnnotator

from skchange.anomaly_detectors.utils import format_multivariate_anomaly_output
from skchange.costs.saving_factory import saving_factory


def check_capa_input(
    X: Union[pd.DataFrame, pd.Series], min_segment_length: int
) -> pd.DataFrame:
    if X.isna().any(axis=None):
        raise ValueError(
            f"X cannot contain missing values: X.isna().sum()={X.isna().sum()}."
        )

    if X.ndim < 2:
        X = X.to_frame()

    n = X.shape[0]
    if n < min_segment_length:
        raise ValueError(
            f"X must have at least min_segment_length samples "
            f"(X.shape[0]={n}, min_segment_length={min_segment_length})."
        )
    return X


def dense_capa_penalty(
    n: int, p: int, n_params: int = 1, scale: float = 1.0
) -> Tuple[float, np.ndarray]:
    """Penalty function for dense anomalies in CAPA.

    Parameters
    ----------
    n : int
        Sample size.
    p : int
        Dimension of the data.
    n_params : int, optional (default=1)
        Number of parameters per segment in the model.
    scale : float, optional (default=1.0)
        Scaling factor for the penalty components.

    Returns
    -------
    alpha : float
        Constant/global penalty term.
    betas : np.ndarray
        Per-component penalty terms.
    """
    psi = np.log(n)
    penalty = scale * (p * n_params + 2 * np.sqrt(p * n_params * psi) + 2 * psi)
    return penalty, np.zeros(p)


def sparse_capa_penalty(
    n: int, p: int, n_params: int = 1, scale: float = 1.0
) -> Tuple[float, np.ndarray]:
    """Penalty function for sparse anomalies in CAPA.

    Parameters
    ----------
    n : int
        Sample size.
    p : int
        Dimension of the data.
    n_params : int, optional (default=1)
        Number of parameters per segment in the model.
    scale : float, optional (default=1.0)
        Scaling factor for the penalty components.

    Returns
    -------
    alpha : float
        Constant/global penalty term.
    betas : np.ndarray
        Per-component penalty terms.
    """
    psi = np.log(n)
    dense_penalty = 2 * scale * psi
    sparse_penalty = 2 * scale * np.log(n_params * p)
    return dense_penalty, np.full(p, sparse_penalty)


def intermediate_capa_penalty(
    n: int, p: int, n_params: int = 1, scale: float = 1.0
) -> Tuple[float, np.ndarray]:
    """Penalty function balancing both dense and sparse anomalies in CAPA.

    Parameters
    ----------
    n : int
        Sample size.
    p : int
        Dimension of the data.
    n_params : int, optional (default=1)
        Number of parameters per segment in the model.
    scale : float, optional (default=1.0)
        Scaling factor for the penalty components.

    Returns
    -------
    alpha : float
        Constant/global penalty term.
    betas : np.ndarray
        Per-component penalty terms.
    """
    if p < 2:
        raise ValueError("p must be at least 2.")

    def penalty_func(j: int) -> float:
        psi = np.log(n)
        c_j = chi2.ppf(1 - j / p, n_params)
        f_j = chi2.pdf(c_j, n_params)
        return scale * (
            2 * (psi + np.log(p))
            + j * n_params
            + 2 * p * c_j * f_j
            + 2 * np.sqrt((j * n_params + 2 * p * c_j * f_j) * (psi + np.log(p)))
        )

    # Penalty function is not defined for j = p.
    penalties = np.vectorize(penalty_func)(np.arange(1, p))
    return 0.0, np.diff(penalties, prepend=0.0, append=penalties[-1])


def combined_capa_penalty(
    n: int, p: int, n_params: int = 1, scale: float = 1.0
) -> Tuple[float, np.ndarray]:
    """Pointwise minimum of dense, sparse and intermediate penalties in CAPA.

    Parameters
    ----------
    n : int
        Sample size.
    p : int
        Dimension of the data.
    n_params : int, optional (default=1)
        Number of parameters per segment in the model.
    scale : float, optional (default=1.0)
        Scaling factor for the penalty components.

    Returns
    -------
    alpha : float
        Constant/global penalty term.
    betas : np.ndarray
        Per-component penalty terms.
    """
    if p < 2:
        return dense_capa_penalty(n, 1, n_params, scale)

    dense_alpha, dense_betas = dense_capa_penalty(n, p, n_params, scale)
    sparse_alpha, sparse_betas = sparse_capa_penalty(n, p, n_params, scale)
    intermediate_alpha, intermediate_betas = intermediate_capa_penalty(
        n, p, n_params, scale
    )
    dense_penalties = dense_alpha + np.cumsum(dense_betas)
    sparse_penalties = sparse_alpha + np.cumsum(sparse_betas)
    intermediate_penalties = intermediate_alpha + np.cumsum(intermediate_betas)
    pointwise_min_penalties = np.zeros(p + 1)
    pointwise_min_penalties[1:] = np.minimum(
        dense_penalties, np.minimum(sparse_penalties, intermediate_penalties)
    )
    return 0.0, np.diff(pointwise_min_penalties)


def capa_penalty_factory(penalty: Union[str, Callable] = "combined") -> Callable:
    """Get a CAPA penalty function.

    Parameters
    ----------
    penalty : str or Callable, optional (default="combined")
        Penalty function to use for CAPA. If a string, must be one of "dense",
        "sparse", "intermediate" or "combined". If a Callable, must be a function
        returning a penalty and per-component penalties, given n, p, n_params and scale.

    Returns
    -------
    penalty_func : Callable
        Penalty function.
    """
    if callable(penalty):
        return penalty
    elif penalty == "dense":
        return dense_capa_penalty
    elif penalty == "sparse":
        return sparse_capa_penalty
    elif penalty == "intermediate":
        return intermediate_capa_penalty
    elif penalty == "combined":
        return combined_capa_penalty
    else:
        raise ValueError(f"Unknown penalty: {penalty}")


@njit
def get_anomalies(
    anomaly_starts: np.ndarray,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    collective_anomalies = []
    point_anomalies = []
    i = anomaly_starts.size - 1
    while i >= 0:
        start_i = anomaly_starts[i]
        size = i - start_i + 1
        if size > 1:
            collective_anomalies.append((int(start_i), i))
            i = int(start_i)
        elif size == 1:
            point_anomalies.append((i, i))
        i -= 1
    return collective_anomalies, point_anomalies


@njit
def penalise_savings(
    savings: np.ndarray, alpha: float, betas: np.ndarray
) -> np.ndarray:
    if np.all(betas < 1e-8):
        penalised_savings = savings.sum(axis=1) - alpha
    if np.all(betas == betas[0]):
        penalised_saving_matrix = np.maximum(savings - betas[0], 0.0) - alpha
        penalised_savings = penalised_saving_matrix.sum(axis=1)
    else:
        n_savings = savings.shape[0]
        penalised_savings = np.zeros(n_savings)
        for i in range(n_savings):
            saving_i = savings[i]
            saving_order = (-saving_i).argsort()  # Decreasing order.
            penalised_saving = np.cumsum(saving_i[saving_order] - betas) - alpha
            argmax = np.argmax(penalised_saving)
            penalised_savings[i] = penalised_saving[argmax]
    return penalised_savings


@njit
def find_affected_components(
    params: Union[np.ndarray, tuple],
    saving_func: Callable,
    anomalies: List[Tuple[int, int]],
    alpha: float,
    betas: np.ndarray,
) -> List[Tuple[int, int, np.ndarray]]:
    new_anomalies = []
    for start, end in anomalies:
        saving = saving_func(params, np.array([start]), np.array([end]))[0]
        saving_order = (-saving).argsort()  # Decreasing order.
        penalised_saving = np.cumsum(saving[saving_order] - betas) - alpha
        argmax = np.argmax(penalised_saving)
        new_anomalies.append((start, end, saving_order[: argmax + 1]))
    return new_anomalies


@njit
def optimise_savings(
    starts: np.ndarray,
    opt_savings: np.ndarray,
    next_savings: np.ndarray,
    alpha: float,
    betas: np.ndarray,
) -> Tuple[float, int]:
    penalised_saving = penalise_savings(next_savings, alpha, betas)
    candidate_savings = opt_savings[starts] + penalised_saving
    argmax = np.argmax(candidate_savings)
    opt_start = starts[0] + argmax
    return candidate_savings[argmax], opt_start


@njit
def run_base_capa(
    X: np.ndarray,
    params: Union[np.ndarray, tuple],
    saving_func: Callable,
    collective_alpha: float,
    collective_betas: np.ndarray,
    point_alpha: float,
    point_betas: np.ndarray,
    min_segment_length: int,
    max_segment_length: int,
) -> Tuple[np.ndarray, List[Tuple[int, int]], List[Tuple[int, int]]]:
    n = X.shape[0]
    opt_savings = np.zeros(n + 1)
    # Store the optimal start and affected components of an anomaly for each t.
    # Used to get the final set of anomalies after the loop.
    opt_anomaly_starts = np.repeat(np.nan, n)

    ts = np.arange(min_segment_length - 1, n)
    for t in ts:
        # Collective anomalies
        lower_start = max(0, t - max_segment_length + 1)
        upper_start = t - min_segment_length + 2
        starts = np.arange(lower_start, upper_start)
        ends = np.repeat(t, len(starts))
        collective_savings = saving_func(params, starts, ends)
        opt_collective_saving, opt_start = optimise_savings(
            starts, opt_savings, collective_savings, collective_alpha, collective_betas
        )

        # Point anomalies
        t_array = np.array([t])
        point_savings = saving_func(params, t_array, t_array)
        opt_point_saving, _ = optimise_savings(
            t_array, opt_savings, point_savings, point_alpha, point_betas
        )

        # Combine and store results
        savings = np.array([opt_savings[t], opt_collective_saving, opt_point_saving])
        argmax = np.argmax(savings)
        opt_savings[t + 1] = savings[argmax]
        if argmax == 1:
            opt_anomaly_starts[t] = opt_start
        elif argmax == 2:
            opt_anomaly_starts[t] = t

    collective_anomalies, point_anomalies = get_anomalies(opt_anomaly_starts)
    return opt_savings[1:], collective_anomalies, point_anomalies


@njit
def run_mvcapa(
    X: np.ndarray,
    saving_func: Callable,
    saving_init_func: Callable,
    collective_alpha: float,
    collective_betas: np.ndarray,
    point_alpha: float,
    point_betas: np.ndarray,
    min_segment_length: int,
    max_segment_length: int,
) -> Tuple[
    np.ndarray, List[Tuple[int, int, np.ndarray]], List[Tuple[int, int, np.ndarray]]
]:
    params = saving_init_func(X)
    opt_savings, collective_anomalies, point_anomalies = run_base_capa(
        X,
        params,
        saving_func,
        collective_alpha,
        collective_betas,
        point_alpha,
        point_betas,
        min_segment_length,
        max_segment_length,
    )
    collective_anomalies = find_affected_components(
        params,
        saving_func,
        collective_anomalies,
        collective_alpha,
        collective_betas,
    )
    point_anomalies = find_affected_components(
        params, saving_func, point_anomalies, point_alpha, point_betas
    )
    return opt_savings, collective_anomalies, point_anomalies


class Mvcapa(BaseSeriesAnnotator):
    """Subset multivariate collective and point anomaly detection.

    An efficient implementation of the MVCAPA algorithm [1]_ for anomaly detection.

    Parameters
    ----------
    saving : str (default="mean")
        Saving function to use for anomaly detection.
    collective_penalty : str or Callable, optional (default="combined")
        Penalty function to use for collective anomalies. If a string, must be one of
        "dense", "sparse", "intermediate" or "combined". If a Callable, must be a
        function returning a penalty and per-component penalties, given n, p, n_params
        and scale.
    collective_penalty_scale : float, optional (default=1.0)
        Scaling factor for the collective penalty.
    point_penalty : str or Callable, optional (default="sparse")
        Penalty function to use for point anomalies. See 'collective_penalty'.
    point_penalty_scale : float, optional (default=1.0)
        Scaling factor for the point penalty.
    min_segment_length : int, optional (default=2)
        Minimum length of a segment.
    max_segment_length : int, optional (default=10000)
        Maximum length of a segment.
    ignore_point_anomalies : bool, optional (default=False)
        If True, detected point anomalies are not returned by .predict(). I.e., only
        collective anomalies are returned.
    fmt : str {"dense", "sparse"}, optional (default="sparse")
        Annotation output format:
        * If "sparse", a sub-series of labels for only the outliers in X is returned,
        * If "dense", a series of labels for all values in X is returned.
    labels : str {"indicator", "score", "int_label"}, optional (default="int_label")
        Annotation output labels:
        * If "indicator", returned values are boolean, indicating whether a value is
        an outlier,
        * If "score", returned values are floats, giving the outlier score.
        * If "int_label", returned values are integer, indicating which segment a
        value belongs to.


    References
    ----------
    .. [1] Fisch, A. T., Eckley, I. A., & Fearnhead, P. (2022). Subset multivariate
    collective and point anomaly detection. Journal of Computational and Graphical
    Statistics, 31(2), 574-585.

    Examples
    --------
    from skchange.anomaly_detectors.capa import Capa
    from skchange.datasets.generate import teeth

    df = teeth(5, 10, p=10, mean=10, affected_proportion=0.2, random_state=2)
    capa = Capa(collective_penalty_scale=5, fmt="sparse", max_segment_length=20)
    capa.fit_predict(df)
    """

    _tags = {
        "capability:missing_values": False,
        "capability:multivariate": True,
        "fit_is_empty": False,
    }

    def __init__(
        self,
        saving: Union[str, Tuple[Callable, Callable]] = "mean",
        collective_penalty: Union[str, Callable] = "combined",
        collective_penalty_scale: float = 2.0,
        point_penalty: Union[str, Callable] = "sparse",
        point_penalty_scale: float = 2.0,
        min_segment_length: int = 2,
        max_segment_length: int = 1000,
        ignore_point_anomalies: bool = False,
        fmt: str = "sparse",
        labels: str = "int_label",
    ):
        self.saving = saving
        self.collective_penalty = collective_penalty
        self.collective_penalty_scale = collective_penalty_scale
        self.point_penalty = point_penalty
        self.point_penalty_scale = point_penalty_scale
        self.min_segment_length = min_segment_length
        self.max_segment_length = max_segment_length
        self.ignore_point_anomalies = ignore_point_anomalies
        super().__init__(fmt=fmt, labels=labels)

        self.saving_func, self.saving_init_func = saving_factory(self.saving)

        if self.min_segment_length < 2:
            raise ValueError("min_segment_length must be at least 2.")
        if self.max_segment_length < self.min_segment_length:
            raise ValueError("max_segment_length must be at least min_segment_length.")

    def _check_X(self, X: Union[pd.DataFrame, pd.Series]) -> pd.DataFrame:
        if X.isna().any(axis=None):
            raise ValueError("X must not contain missing values.")

        if X.ndim < 2:
            X = X.to_frame()

        n = X.shape[0]
        if n < self.min_segment_length:
            raise ValueError(
                f"X must have at least min_segment_length samples "
                f"(X.shape[0]={n}, min_segment_length={self.min_segment_length})."
            )
        return X

    def _get_penalty_components(self, X: pd.DataFrame) -> Tuple[np.ndarray, float]:
        # TODO: Add penalty tuning.
        # if self.tune:
        #     return self._tune_threshold(X)
        n = X.shape[0]
        p = X.shape[1]
        n_params = 1  # TODO: Add support for depending on 'score'.
        collective_penalty_func = capa_penalty_factory(self.collective_penalty)
        collective_alpha, collective_betas = collective_penalty_func(
            n, p, n_params, scale=self.collective_penalty_scale
        )
        point_penalty_func = capa_penalty_factory(self.point_penalty)
        point_alpha, point_betas = point_penalty_func(
            n, p, n_params, scale=self.point_penalty_scale
        )
        return collective_alpha, collective_betas, point_alpha, point_betas

    def _fit(self, X: pd.DataFrame, Y: Optional[pd.DataFrame] = None):
        """Fit to training data.

        Trains the threshold on the input data if `tune` is True. Otherwise, the
        threshold is set to the input `threshold` value if provided. If not,
        it is set to the default value for the test statistic, which depends on
        the dimension of X.

        Parameters
        ----------
        X : pd.DataFrame
            training data to fit the threshold to.
        Y : pd.Series, optional
            Does nothing. Only here to make the fit method compatible with sktime
            and scikit-learn.

        Returns
        -------
        self : returns a reference to self

        State change
        ------------
        creates fitted model (attributes ending in "_")
        """
        X = check_capa_input(X, self.min_segment_length)
        penalty_components = self._get_penalty_components(X)
        self.collective_alpha_ = penalty_components[0]
        self.collective_betas_ = penalty_components[1]
        self.point_alpha_ = penalty_components[2]
        self.point_betas_ = penalty_components[3]
        return self

    def _predict(self, X: Union[pd.DataFrame, pd.Series]) -> pd.Series:
        """Create annotations on test/deployment data.

        core logic

        Parameters
        ----------
        X : pd.DataFrame - data to annotate, time series

        Returns
        -------
        Y : pd.Series - annotations for sequence X
            exact format depends on annotation type
        """
        X = check_capa_input(X, self.min_segment_length)
        opt_savings, self.collective_anomalies, self.point_anomalies = run_mvcapa(
            X.values,
            self.saving_func,
            self.saving_init_func,
            self.collective_alpha_,
            self.collective_betas_,
            self.point_alpha_,
            self.point_betas_,
            self.min_segment_length,
            self.max_segment_length,
        )
        self.scores = np.diff(opt_savings, prepend=0.0)
        anomalies = format_multivariate_anomaly_output(
            self.fmt,
            self.labels,
            X.shape[0],
            X.shape[1],
            self.collective_anomalies,
            self.point_anomalies if not self.ignore_point_anomalies else None,
            X.index,
            X.columns,
            self.scores,
        )
        return anomalies

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.
            There are currently no reserved values for annotators.

        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        params = [
            {"saving": "mean", "min_segment_length": 2},
            {"saving": "mean", "collective_penalty_scale": 0, "min_segment_length": 2},
        ]
        return params
