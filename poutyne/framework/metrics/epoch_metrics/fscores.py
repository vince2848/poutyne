"""
The source code of this file was copied from the AllenNLP project, and has been modified.

Copyright 2019 AllenNLP

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from typing import Optional, Union, List, Tuple
import torch
from .base import EpochMetric
from ..metrics_registering import register_epoch_metric


class FBeta(EpochMetric):
    """
    The source code of this class is under the Apache v2 License and was copied from
    the AllenNLP project and has been modified.

    Compute precision, recall, F-measure and support for each class.

    The precision is the ratio ``tp / (tp + fp)`` where ``tp`` is the number of
    true positives and ``fp`` the number of false positives. The precision is
    intuitively the ability of the classifier not to label as positive a sample
    that is negative.

    The recall is the ratio ``tp / (tp + fn)`` where ``tp`` is the number of
    true positives and ``fn`` the number of false negatives. The recall is
    intuitively the ability of the classifier to find all the positive samples.

    The F-beta score can be interpreted as a weighted harmonic mean of
    the precision and recall, where an F-beta score reaches its best
    value at 1 and worst score at 0.

    If we have precision and recall, the F-beta score is simply:
    ``F-beta = (1 + beta ** 2) * precision * recall / (beta ** 2 * precision + recall)``

    The F-beta score weights recall more than precision by a factor of
    ``beta``. ``beta == 1.0`` means recall and precision are equally important.

    The support is the number of occurrences of each class in ``y_true``.

    Keys in :class:`logs<poutyne.Callback>` dictionary of callbacks:
        - Train: ``'{metric}_{average}'``
        - Validation: ``'val_{metric}_{average}'``

        where ``{metric}`` and ``{average}`` are replaced by the value of their
        respective parameters.

    Args:
        metric (Optional[str]): One of {'fscore', 'precision', 'recall'}.
            Whether to return the F-score, the precision or the recall. When not
            provided, all three metrics are returned. (Default value = None)
        average (Union[str, int]): One of {'micro' (default), 'macro', label_number}
            If the argument is of type integer, the score for this class (the label number) is calculated.
            Otherwise, this determines the type of averaging performed on the data:

            ``'binary'``:
                Calculate metrics with regard to a single class identified by the
                `pos_label` argument. This is equivalent to `average=pos_label` except
                that the binary mode is enforced, i.e. an exception will be raised if
                there are more than two prediction scores.
            ``'micro'``:
                Calculate metrics globally by counting the total true positives,
                false negatives and false positives.
            ``'macro'``:
                Calculate metrics for each label, and find their unweighted mean.
                This does not take label imbalance into account.

            (Default value = 'micro')
        beta (float):
            The strength of recall versus precision in the F-score. (Default value = 1.0)
        pos_label (int):
            The class with respect to which the metric is computed when `average == 'binary'`. Otherwise, this
            argument has no effect. (Default value = 1)
        ignore_index (int): Specifies a target value that is ignored. This also works in combination with
            a mask if provided. (Default value = -100)
        names (Optional[Union[str, List[str]]]): The names associated to the metrics. It is a string when
            a single metric is requested. It is a list of 3 strings if all metrics are requested.
            (Default value = None)
    """

    def __init__(
        self,
        *,
        metric: Optional[str] = None,
        average: Union[str, int] = 'macro',
        beta: float = 1.0,
        pos_label: int = 1,
        ignore_index: int = -100,
        names: Optional[Union[str, List[str]]] = None,
    ) -> None:
        super().__init__()
        self.metric_options = ('fscore', 'precision', 'recall')
        if metric is not None and metric not in self.metric_options:
            raise ValueError(f"`metric` has to be one of {self.metric_options}.")

        average_options = ('binary', 'micro', 'macro')
        if average not in average_options and not isinstance(average, int):
            raise ValueError(f"`average` has to be one of {average_options} or an integer.")

        if beta <= 0:
            raise ValueError("`beta` should be >0 in the F-beta score.")

        self._metric = metric
        self._average = average if average in average_options else None
        self._label = None
        if isinstance(average, int):
            self._label = average
        elif average == 'binary':
            self._label = pos_label
        self._beta = beta
        self.ignore_index = ignore_index
        self.__name__ = self._get_names(names)

        # statistics
        # the total number of true positive instances under each class
        # Shape: (num_classes, )
        self.register_buffer('_true_positive_sum', None)
        # the total number of instances
        # Shape: (num_classes, )
        self.register_buffer('_total_sum', None)
        # the total number of instances under each _predicted_ class,
        # including true positives and false positives
        # Shape: (num_classes, )
        self.register_buffer('_pred_sum', None)
        # the total number of instances under each _true_ class,
        # including true positives and false negatives
        # Shape: (num_classes, )
        self.register_buffer('_true_sum', None)

    def _get_name(self, metric):
        name = metric
        if self._average is not None:
            name += '_' + self._average
        if self._label is not None:
            name += '_' + str(self._label)
        return name

    def _get_names(self, names):
        if self._metric is None:
            default_name = list(map(self._get_name, self.metric_options))
        else:
            default_name = self._get_name(self._metric)

        if names is not None:
            self._validate_supplied_names(names, default_name)
            return names

        return default_name

    def _validate_supplied_names(self, names, default_name):
        names_list = [names] if isinstance(names, str) else names
        default_name = [default_name] if isinstance(default_name, str) else default_name
        if len(names_list) != len(default_name):
            raise ValueError(f"`names` should contain names for the following metrics: {', '.join(default_name)}.")

    def forward(self, y_pred: torch.Tensor, y_true: Union[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]) -> None:
        # pylint: disable=too-many-branches
        """
        Update the confusion matrix for calculating the F-score.

        Args:
            y_pred (torch.Tensor): A tensor of predictions of shape (batch_size, num_classes, ...).
            y_true (Union[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]):
                Ground truths. A tensor of the integer class label of shape (batch_size, ...). It must
                be the same shape as the ``y_pred`` tensor without the ``num_classes`` dimension.
                It can also be a tuple with two tensors of the same shape, the first being the
                ground truths and the second being a mask.
        """

        mask = 1
        if isinstance(y_true, tuple):
            y_true, mask = y_true
            mask = mask.byte()

        if self.ignore_index is not None:
            mask *= (y_true != self.ignore_index).byte()

        if not torch.is_tensor(mask):
            mask = torch.ones_like(y_true, dtype=torch.bool)
        else:
            mask = mask.bool()

        # Calculate true_positive_sum, true_negative_sum, pred_sum, true_sum
        num_classes = y_pred.size(1)
        if (y_true >= num_classes).any():
            raise ValueError(
                f"A gold label passed to FBetaMeasure contains an id >= {num_classes}, the number of classes."
            )

        if self._average == 'binary' and num_classes > 2:
            raise ValueError("When `average` is binary, the number of prediction scores must be 2.")

        # It means we call this metric at the first time
        # when `self._true_positive_sum` is None.
        if self._true_positive_sum is None:
            self._true_positive_sum = torch.zeros(num_classes, device=y_pred.device)
            self._true_sum = torch.zeros(num_classes, device=y_pred.device)
            self._pred_sum = torch.zeros(num_classes, device=y_pred.device)
            self._total_sum = torch.zeros(num_classes, device=y_pred.device)

        y_true = y_true.float()

        argmax_y_pred = y_pred.argmax(1).float()
        true_positives = (y_true == argmax_y_pred) * mask
        true_positives_bins = y_true[true_positives]

        # Watch it:
        # The total numbers of true positives under all _predicted_ classes are zeros.
        if true_positives_bins.shape[0] == 0:
            true_positive_sum = torch.zeros(num_classes, device=y_pred.device)
        else:
            true_positive_sum = torch.bincount(true_positives_bins.long(), minlength=num_classes).float()

        pred_bins = argmax_y_pred[mask].long()
        # Watch it:
        # When the `mask` is all 0, we will get an _empty_ tensor.
        if pred_bins.shape[0] != 0:
            pred_sum = torch.bincount(pred_bins, minlength=num_classes).float()
        else:
            pred_sum = torch.zeros(num_classes, device=y_pred.device)

        y_true_bins = y_true[mask].long()
        if y_true.shape[0] != 0:
            true_sum = torch.bincount(y_true_bins, minlength=num_classes).float()
        else:
            true_sum = torch.zeros(num_classes, device=y_pred.device)

        self._true_positive_sum += true_positive_sum
        self._pred_sum += pred_sum
        self._true_sum += true_sum
        self._total_sum += mask.sum().to(torch.float)

    def get_metric(self) -> Union[float, List[float]]:
        """
        Returns either a float if a single metric is set in the ``__init__`` or a list
        of floats [f-score, precision, recall] if all metrics are requested.
        """
        if self._true_positive_sum is None:
            raise RuntimeError("You never call this metric before.")

        tp_sum = self._true_positive_sum
        pred_sum = self._pred_sum
        true_sum = self._true_sum

        if self._average == 'micro':
            tp_sum = tp_sum.sum()
            pred_sum = pred_sum.sum()
            true_sum = true_sum.sum()

        beta2 = self._beta ** 2
        # Finally, we have all our sufficient statistics.
        precision = _prf_divide(tp_sum, pred_sum)
        recall = _prf_divide(tp_sum, true_sum)
        fscore = (1 + beta2) * precision * recall / (beta2 * precision + recall)
        fscore[tp_sum == 0] = 0.0

        if self._average == 'macro':
            precision = precision.mean()
            recall = recall.mean()
            fscore = fscore.mean()

        if self._label is not None:
            # Retain only selected labels and order them
            precision = precision[self._label]
            recall = recall[self._label]
            fscore = fscore[self._label]

        if self._metric is None:
            return [fscore.item(), precision.item(), recall.item()]

        if self._metric == 'fscore':
            return fscore.item()
        if self._metric == 'precision':
            return precision.item()
        # if self._metric == 'recall':
        return recall.item()

    def reset(self) -> None:
        self._true_positive_sum = None
        self._pred_sum = None
        self._true_sum = None
        self._total_sum = None


@register_epoch_metric
class F1(FBeta):
    """
    Alias class for :class:`~poutyne.FBeta` where ``metric == 'fscore'`` and ``beta == 1``.

    Possible string name in :class:`batch_metrics argument <poutyne.Model>`:
        - ``'f1'``

    Keys in :class:`logs<poutyne.Callback>` dictionary of callbacks:
        - Train: ``'fscore_{average}'``
        - Validation: ``'val_fscore_{average}'``

        where ``{average}`` is replaced by the value of the respective parameter.
    """

    def __init__(self, average='macro'):
        super().__init__(metric='fscore', average=average, beta=1)


@register_epoch_metric
class Precision(FBeta):
    """
    Alias class for :class:`~poutyne.FBeta` where ``metric == 'precision'`` and ``beta == 1``.

    Possible string name in :class:`batch_metrics argument <poutyne.Model>`:
        - ``'precision'``

    Keys in :class:`logs<poutyne.Callback>` dictionary of callbacks:
        - Train: ``'precision_{average}'``
        - Validation: ``'val_precision_{average}'``

        where ``{average}`` is replaced by the value of the respective parameter.
    """

    def __init__(self, average='macro'):
        super().__init__(metric='precision', average=average, beta=1)


@register_epoch_metric
class Recall(FBeta):
    """
    Alias class for :class:`~poutyne.FBeta` where ``metric == 'recall'`` and ``beta == 1``.

    Possible string name in :class:`batch_metrics argument <poutyne.Model>`:
        - ``'recall'``

    Keys in :class:`logs<poutyne.Callback>` dictionary of callbacks:
        - Train: ``'recall_{average}'``
        - Validation: ``'val_recall_{average}'``

        where ``{average}`` is replaced by the value of the respective parameter.
    """

    def __init__(self, average='macro'):
        super().__init__(metric='recall', average=average, beta=1)


def _prf_divide(numerator, denominator):
    """Performs division and handles divide-by-zero.

    On zero-division, sets the corresponding result elements to zero.
    """
    result = numerator / denominator
    mask = denominator == 0.0
    if not mask.any():
        return result

    # remove nan
    result[mask] = 0.0
    return result
