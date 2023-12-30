"""Seeded binary segmentation algorithm for multiple changepoint detection."""

__author__ = ["mtveten"]
__all__ = ["SeededBinarySegmentation"]

from typing import Callable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from numba import njit
from sktime.annotation.base import BaseSeriesAnnotator

from skchange.change_detectors.utils import format_changepoint_output
from skchange.scores.score_factory import score_factory
from skchange.utils.validation.data import check_data
from skchange.utils.validation.parameters import check_in_interval, check_larger_than


@njit
def make_seeded_intervals(
    n: int,
    min_interval_length: int,
    max_interval_length: int,
    growth_factor: float = 1.5,
) -> Tuple[np.ndarray, np.ndarray]:
    starts = [int(0)]  # For numba to be able to compile type.
    ends = [int(0)]  # For numba to be able to compile type.
    interval_len = min_interval_length
    max_interval_length = min(max_interval_length, n)
    step_factor = 1 - 1 / growth_factor
    while interval_len <= max_interval_length:
        level_starts = [int(0)]
        level_ends = [int(interval_len - 1)]
        step = max(1, np.floor(step_factor * interval_len))
        while level_ends[-1] < n - 1:
            start = level_starts[-1] + step
            level_starts.append(int(start))
            level_ends.append(int(min(start + interval_len - 1, n - 1)))
        interval_len = max(interval_len + 1, np.floor(growth_factor * interval_len))
        starts += level_starts
        ends += level_ends
    return np.array(starts[1:]), np.array(ends[1:])


@njit
def greedy_changepoint_selection(
    scores: np.ndarray,
    maximizers: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    threshold: float,
) -> List[int]:
    scores = scores.copy()
    cpts = []
    while np.any(scores > threshold):
        argmax = scores.argmax()
        cpt = maximizers[argmax]
        cpts.append(int(cpt))
        scores[(cpt >= starts) & (cpt < ends)] = 0.0
    cpts.sort()
    return cpts


@njit
def run_seeded_binary_segmentation(
    X: np.ndarray,
    score_func: Callable,
    score_init_func: Callable,
    threshold: float,
    min_segment_length: int,
    max_interval_length: int,
    growth_factor: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    starts, ends = make_seeded_intervals(
        X.shape[0],
        2 * min_segment_length,
        max_interval_length,
        growth_factor,
    )
    params = score_init_func(X)

    amoc_scores = np.zeros(starts.size)
    maximizers = np.zeros(starts.size)
    for i, (start, end) in enumerate(zip(starts, ends)):
        scores = np.zeros(end - start)
        splits_lower = start + min_segment_length - 1
        splits = range(splits_lower, end - min_segment_length + 1)
        for k, split in enumerate(splits):
            scores[k] = score_func(params, start, end, split)
        argmax = np.argmax(scores)
        amoc_scores[i] = scores[argmax]
        maximizers[i] = splits_lower + argmax

    cpts = greedy_changepoint_selection(
        amoc_scores, maximizers, starts, ends, threshold
    )
    return cpts, amoc_scores, maximizers, starts, ends


class SeededBinarySegmentation(BaseSeriesAnnotator):
    """Seeded binary segmentation algorithm for multiple changepoint detection.

    Binary segmentation type changepoint detection algorithms recursively split the data
    into two segments, and test whether the two segments are different. The seeded
    binary segmentation algorithm is an efficient version of such algorithms, which
    tests for changepoints in intervals of exponentially growing length. It has the same
    theoretical guarantees as the original binary segmentation algorithm, but runs
    in log-linear time no matter the changepoint configuration.

    Efficently implemented using numba.

    Parameters
    ----------
    fmt : str {"dense", "sparse"}, optional (default="sparse")
        Annotation output format:
        * If "sparse", a sub-series of labels for only the outliers in X is returned,
        * If "dense", a series of labels for all values in X is returned.
    labels : str {"indicator", "score", "int_label"}, optional (default="int_label")
        Annotation output labels:
        * If "indicator", returned values are boolean, indicating whether a value is an
        outlier,
        * If "score", returned values are floats, giving the outlier score.
        * If "int_label", returned values are integer, indicating which segment a value
        belongs to.
    score: str, Tuple[Callable, Callable], optional (default="mean")
        Test statistic to use for changepoint detection.
        * If "mean", the difference-in-mean statistic is used,
        * If "var", the difference-in-variance statistic is used,
        * If a tuple, it must contain two functions: The first function is the scoring
        function, which takes in the output of the second function as its first
        argument, and start, end and split indices as the second, third and fourth
        arguments. The second function is the initializer, which precomputes quantities
        that should be precomputed. See skchange/scores/score_factory.py for examples.
    threshold_scale : float, optional (default=2.0)
        Scaling factor for the threshold. The threshold is set to
        'threshold_scale * 2 * p * np.sqrt(np.log(n))', where 'n' is the sample size
        and 'p' is the number of variables. If None, the threshold is tuned on the data
        input to .fit().
    level : float, optional (default=0.01)
        If `threshold_scale` is None, the threshold is set to the (1-`level`)-quantile
        of the changepoint scores of all the seeded intervals on the training data.
        For this to be correct, the training data must contain no changepoints.
    min_segment_length : int, optional (default=5)
        Minimum length between two changepoints. Must be greater than or equal to 1.
    max_interval_length : int (default=200)
        The maximum length of an interval to estimate a changepoint in. Must be greater
        than or equal to '2 * min_segment_length'.
    growth_factor : float (default = 1.5)
        The growth factor for the seeded intervals. Intervals grow in size according to
        'interval_len=max(interval_len + 1, np.floor(growth_factor * interval_len))',
        starting at 'interval_len'='min_interval_length'. It also governs the amount
        of overlap between intervals of the same length, as the start of each interval
        is shifted by a factor of '1 + 1 / growth_factor'. Must be a float in (1, 2].

    References
    ----------
    .. [1] Kovács, S., Bühlmann, P., Li, H., & Munk, A. (2023). Seeded binary
    segmentation: a general methodology for fast and optimal changepoint detection.
    Biometrika, 110(1), 249-256.

    Examples
    --------
    from skchange.change_detectors.binary_segmentation import SeededBinarySegmentation
    from skchange.datasets.generate import teeth

    # Generate data
    df = teeth(n_segments=2, mean=10, segment_length=100000, p=5, random_state=2)

    # Detect changepoints
    detector = SeededBinarySegmentation()
    detector.fit_predict(df)
    """

    _tags = {
        "capability:missing_values": False,
        "capability:multivariate": True,
        "fit_is_empty": False,
    }

    def __init__(
        self,
        score: Union[str, Tuple[Callable, Callable]] = "mean",
        threshold_scale: Optional[float] = 2.0,
        level: float = 1e-8,
        min_segment_length: int = 5,
        max_interval_length: int = 200,
        growth_factor: float = 1.5,
        fmt: str = "sparse",
        labels: str = "int_label",
    ):
        self.score = score
        self.threshold_scale = threshold_scale  # Just holds the input value.
        self.level = level
        self.min_segment_length = min_segment_length
        self.max_interval_length = max_interval_length
        self.growth_factor = growth_factor
        super().__init__(fmt=fmt, labels=labels)
        self.score_f, self.score_init_f = score_factory(self.score)

        check_larger_than(0.0, self.threshold_scale, "threshold_scale", allow_none=True)
        check_in_interval(pd.Interval(0.0, 1.0, closed="neither"), self.level, "level")
        check_larger_than(1.0, self.min_segment_length, "min_segment_length")
        check_larger_than(
            2 * self.min_segment_length, self.max_interval_length, "max_interval_length"
        )
        check_in_interval(
            pd.Interval(1.0, 2.0, closed="right"),
            self.growth_factor,
            "growth_factor",
        )

    def _tune_threshold(self, X: pd.DataFrame) -> float:
        """Tune the threshold.

        The threshold is set to the (1-`level`)-quantile of the changepoint scores from
        all the seeded intervals on the training data `X`. For this to be correct, the
        training data must contain no changepoints.

        Parameters
        ----------
        X : pd.DataFrame
            Training data to tune the threshold on.

        Returns
        -------
        threshold : float
            The tuned threshold.
        """
        _, scores, _, _, _ = run_seeded_binary_segmentation(
            X.values,
            self.score_f,
            self.score_init_f,
            np.inf,
            self.min_segment_length,
            self.max_interval_length,
            self.growth_factor,
        )
        return np.quantile(scores, 1 - self.level)

    @staticmethod
    def get_default_threshold(n: int, p: int) -> float:
        """Get the default threshold for Seeded Binary Segmentation.

        Parameters
        ----------
        n : int
            Sample size.
        p : int
            Number of variables.

        Returns
        -------
        threshold : float
            The default threshold.
        """
        return 2 * p * np.sqrt(np.log(n))

    def _get_threshold(self, X: pd.DataFrame) -> float:
        if self.threshold_scale is None:
            return self._tune_threshold(X)
        else:
            n = X.shape[0]
            p = X.shape[1]
            return self.threshold_scale * self.get_default_threshold(n, p)

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
        """
        X = check_data(
            X,
            min_length=2 * self.min_segment_length,
            min_length_name="min_interval_length",
        )
        self.threshold_ = self._get_threshold(X)
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
        X = check_data(
            X,
            min_length=2 * self.min_segment_length,
            min_length_name="min_interval_length",
        )
        cpts, scores, maximizers, starts, ends = run_seeded_binary_segmentation(
            X.values,
            self.score_f,
            self.score_init_f,
            self.threshold_,
            self.min_segment_length,
            self.max_interval_length,
            self.growth_factor,
        )
        self.changepoints = cpts
        self.scores = pd.DataFrame(
            {"start": starts, "end": ends, "maximizer": maximizers, "score": scores}
        )
        self.per_sample_scores = (
            self.scores.groupby("maximizer")["score"]
            .max()
            .reindex(range(X.shape[0]), fill_value=0)
        ).values
        return format_changepoint_output(
            self.fmt, self.labels, self.changepoints, X.index, self.per_sample_scores
        )

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
            {"score": "mean"},
            {"score": "mean", "threshold_scale": 0.0},
        ]
        return params
