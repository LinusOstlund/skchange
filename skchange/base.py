"""Detector base class.

    class name: BaseDetector

    Adapted from the sktime.BaseSeriesAnnotator class.

Scitype defining methods:
    fitting                         - fit(self, X, Y=None)
    detecting, sparse format        - predict(self, X)
    detecting, dense format         - transform(self, X)
    detection scores, dense         - score_transform(self, X)  [optional]
    updating (temporal)             - update(self, X, Y=None)  [optional]

Each detector type (e.g. anomaly detector, collective anomaly detector, changepoint
detector) are subclasses of BaseDetector (task + learning_type tags in sktime).
They are defined by the content and format of the output of the predict method. Each
detector type therefore has the following methods for converting between sparse and
dense output formats:
    converting sparse output to dense - sparse_to_dense(y_sparse, index)
    converting dense output to sparse - dense_to_sparse(y_dense)  [optional]

Convenience methods:
    update&detect   - update_predict(self, X)
    fit&detect      - fit_predict(self, X, Y=None)
    fit&transform   - fit_transform(self, X, Y=None)

Inspection methods:
    hyper-parameter inspection  - get_params()
    fitted parameter inspection - get_fitted_params()

State:
    fitted model/strategy   - by convention, any attributes ending in "_"
    fitted state flag       - check_is_fitted()
"""

__author__ = ["mtveten"]
__all__ = ["BaseDetector"]

from sktime.base import BaseEstimator
from sktime.utils.validation.series import check_series


class BaseDetector(BaseEstimator):
    """Base detector.

    An alternative implementation to the BaseSeriesAnnotator class from sktime,
    more focused on the detection of events of interest.
    Safer for now since the annotation module is still experimental.

    All detectors share the common feature that each element of the output from .predict
    indicates the detection of a specific event of interest, such as an anomaly, a
    changepoint, or something else.

    Needs to be implemented:
    - _fit(self, X, Y=None) -> self
    - _predict(self, X) -> pd.Series or pd.DataFrame
    - sparse_to_dense(y_sparse, index) -> pd.Series or pd.DataFrame
        * Enables the transform method to work.

    Optional to implement:
    - dense_to_sparse(y_dense) -> pd.Series or pd.DataFrame
    - _score_transform(self, X) -> pd.Series or pd.DataFrame
    - _update(self, X, Y=None) -> self
    """

    _tags = {
        "object_type": "estimator",  # sktime scitype of object
        "learning_type": "None",  # Tag to determine test in test_all_annotators
        "task": "None",  # Tag to determine test in test_all_annotators
        #
        # todo: distribution_type? we may have to refactor this, seems very soecufuc
        "distribution_type": "None",  # Tag to determine test in test_all_annotators
    }  # for unit test cases

    def __init__(self):
        self.task = self.get_class_tag("task")
        self.learning_type = self.get_class_tag("learning_type")

        self._is_fitted = False

        self._X = None
        self._Y = None

        super().__init__()

    def fit(self, X, Y=None):
        """Fit to training data.

        Parameters
        ----------
        X : pd.DataFrame
            Training data to fit model to (time series).
        Y : pd.Series, optional
            Ground truth annotations for training if annotator is supervised.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Creates fitted model that updates attributes ending in "_". Sets
        _is_fitted flag to True.
        """
        X = check_series(X, allow_index_names=True)

        if Y is not None:
            Y = check_series(Y, allow_index_names=True)

        self._X = X
        self._Y = Y

        # fkiraly: insert checks/conversions here, after PR #1012 I suggest

        self._fit(X=X, Y=Y)

        # this should happen last
        self._is_fitted = True

        return self

    def _fit(self, X, Y=None):
        """Fit to training data.

        core logic

        Parameters
        ----------
        X : pd.DataFrame
            Training data to fit model to time series.
        Y : pd.Series, optional
            Ground truth annotations for training if annotator is supervised.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Updates fitted model that updates attributes ending in "_".
        """
        raise NotImplementedError("abstract method")

    def predict(self, X):
        """Detect events in test/deployment data.

        Parameters
        ----------
        X : pd.DataFrame
            Data to detect events in (time series).

        Returns
        -------
        Y : pd.Series or pd.DataFrame
            Each element or row corresponds to a detected event. Exact format depends on
            the specific detector type.
        """
        self.check_is_fitted()

        X = check_series(X, allow_index_names=True)

        # fkiraly: insert checks/conversions here, after PR #1012 I suggest

        Y = self._predict(X=X)

        return Y

    def _predict(self, X):
        """Create annotations on test/deployment data.

        core logic

        Parameters
        ----------
        X : pd.DataFrame
            Data to annotate, time series.

        Returns
        -------
        Y : pd.Series
            Annotations for sequence X exact format depends on annotation type.
        """
        raise NotImplementedError("abstract method")

    def transform(self, X):
        """Detect events and return the result in a dense format.

        Parameters
        ----------
        X : pd.DataFrame
            Data to detect events in (time series).

        Returns
        -------
        Y : pd.Series or pd.DataFrame
            Detections for sequence X. The returned detections will be in the dense
            format, meaning that each element in X will be annotated according to the
            detection results in some meaningful way depending on the detector type.
        """
        Y = self.predict(X)
        return self.sparse_to_dense(Y, X.index)

    @staticmethod
    def sparse_to_dense(y_sparse, index):
        """Convert the sparse output from a detector to a dense format.

        Parameters
        ----------
        y_sparse : pd.Series
            The sparse output from a detector's predict method. The format of the
            series depends on the task and capability of the annotator.
        index : array-like
            Indices that are to be annotated according to ``y_sparse``.

        Returns
        -------
        pd.Series
        """
        raise NotImplementedError("abstract method")

    @staticmethod
    def dense_to_sparse(y_dense):
        """Convert the dense output from a detector to a sparse format.

        Parameters
        ----------
        y_dense : pd.Series
            The dense output from a detector's transform method. The format of the
            series depends on the task and capability of the annotator.

        Returns
        -------
        pd.Series
        """
        raise NotImplementedError("abstract method")

    def score_transform(self, X):
        """Return scores for predicted annotations on test/deployment data.

        Parameters
        ----------
        X : pd.DataFrame
            Data to annotate (time series).

        Returns
        -------
        Y : pd.Series
            Scores for sequence X exact format depends on annotation type.
        """
        self.check_is_fitted()
        X = check_series(X, allow_index_names=True)
        return self._score_transform(X)

    def _score_transform(self, X):
        """Return scores for predicted annotations on test/deployment data.

        core logic

        Parameters
        ----------
        X : pd.DataFrame
            Data to annotate, time series.

        Returns
        -------
        Y : pd.Series
            One score for each element in X.
            Annotations for sequence X exact format depends on annotation type.
        """
        raise NotImplementedError("abstract method")

    def update(self, X, Y=None):
        """Update model with new data and optional ground truth annotations.

        Parameters
        ----------
        X : pd.DataFrame
            Training data to update model with (time series).
        Y : pd.Series, optional
            Ground truth annotations for training if annotator is supervised.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Updates fitted model that updates attributes ending in "_".
        """
        self.check_is_fitted()

        X = check_series(X, allow_index_names=True)

        if Y is not None:
            Y = check_series(Y, allow_index_names=True)

        self._X = X.combine_first(self._X)

        if Y is not None:
            self._Y = Y.combine_first(self._Y)

        self._update(X=X, Y=Y)

        return self

    def _update(self, X, Y=None):
        """Update model with new data and optional ground truth annotations.

        core logic

        Parameters
        ----------
        X : pd.DataFrame
            Training data to update model with time series
        Y : pd.Series, optional
            Ground truth annotations for training if annotator is supervised.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Updates fitted model that updates attributes ending in "_".
        """
        # default/fallback: re-fit to all data
        self._fit(self._X, self._Y)

        return self

    def update_predict(self, X):
        """Update model with new data and create annotations for it.

        Parameters
        ----------
        X : pd.DataFrame
            Training data to update model with, time series.

        Returns
        -------
        Y : pd.Series
            Annotations for sequence X exact format depends on annotation type.

        Notes
        -----
        Updates fitted model that updates attributes ending in "_".
        """
        X = check_series(X, allow_index_names=True)

        self.update(X=X)
        Y = self.predict(X=X)

        return Y

    def fit_predict(self, X, Y=None):
        """Fit to data, then predict it.

        Fits model to X and Y with given annotation parameters
        and returns the annotations made by the model.

        Parameters
        ----------
        X : pd.DataFrame, pd.Series or np.ndarray
            Data to be transformed
        Y : pd.Series or np.ndarray, optional (default=None)
            Target values of data to be predicted.

        Returns
        -------
        self : pd.Series
            Annotations for sequence X exact format depends on annotation type.
        """
        # Non-optimized default implementation; override when a better
        # method is possible for a given algorithm.
        return self.fit(X, Y).predict(X)

    def fit_transform(self, X, Y=None):
        """Fit to data, then transform it.

        Fits model to X and Y with given annotation parameters
        and returns the annotations made by the model.

        Parameters
        ----------
        X : pd.DataFrame, pd.Series or np.ndarray
            Data to be transformed
        Y : pd.Series or np.ndarray, optional (default=None)
            Target values of data to be predicted.

        Returns
        -------
        self : pd.Series
            Annotations for sequence X exact format depends on annotation type.
        """
        Y = self.fit_predict(X)
        return self.sparse_to_dense(Y, index=X.index)


# Notes on required .predict output formats per detector type (task and capability):
#
# - task == "anomaly_detection":
#     pd.Series(anomaly_indices, dtype=int, name="anomalies)
# - task == "collective_anomaly_detection":
#     pd.Series(pd.IntervalIndex(
#         anomaly_intervals, closed=<insert>, name="collective_anomalies"
#     ))
# - task == "change_point_detection":
#     Changepoints are defined as the last element of a segment.
#     pd.Series(changepoint_indices, dtype=int, name="changepoints")
# - task == "segmentation":
#     Difference from change point detection: Allows the same label to be assigned to
#     multiple segments.
#     pd.Series({
#         index = pd.IntervalIndex(segment_intervals, closed=<insert>),
#         values = segment_labels,
#     })
# - task == "None":
#     Custom task.
#     Only restriction is that the output must be a pd.Series or pd.DataFrame where
#     each element or row corresponds to a detected event.
#     For .transform to work, .sparse_to_dense must be implemented for custom tasks.
# - capability:subset_detection is True:
#     * task == "anomaly_detection":
#         pd.DataFrame({
#             "location": anomaly_indices,
#             "columns": affected_components_list,
#         })
#     * task == "collective_anomaly_detection":
#         pd.DataFrame({
#             "location": pd.IntervalIndex(anomaly_intervals, closed=<insert>),
#             "columns": affected_components_list,
#         })
#     * task == "change_point_detection":
#         pd.DataFrame({
#             "location": changepoint_indices,
#             "columns": affected_components_list,
#         })
# - capability:detection_score is True: Explicit way of stating that _score_transform
#   is implemented.
