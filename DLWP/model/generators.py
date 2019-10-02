#
# Copyright (c) 2019 Jonathan Weyn <jweyn@uw.edu>
#
# See the file LICENSE for your rights.
#

"""
High-level APIs for building data generators. These produce batches of data on-the-fly for DLWP models'
fit_generator() methods.
"""

import warnings
import numpy as np
import xarray as xr
from keras.utils import Sequence
from ..util import delete_nan_samples, insolation, to_bool


class DataGenerator(Sequence):
    """
    Class used to generate training data on the fly from a loaded DataSet of predictor data. Depends on the structure
    of the EnsembleSelector to do scaling and imputing of data.
    """

    def __init__(self, model, ds, batch_size=32, shuffle=False, remove_nan=True):
        """
        Initialize a DataGenerator.

        :param model: instance of a DLWP model
        :param ds: xarray Dataset: predictor dataset. Should have attributes 'predictors' and 'targets'
        :param batch_size: int: number of samples to take at a time from the dataset
        :param shuffle: bool: if True, randomly select batches
        :param remove_nan: bool: if True, remove any samples with NaNs
        """
        self.model = model
        if not hasattr(ds, 'predictors') or not hasattr(ds, 'targets'):
            raise ValueError("dataset must have 'predictors' and 'targets' variables")
        self.ds = ds
        self._batch_size = batch_size
        self._shuffle = shuffle
        self._remove_nan = remove_nan
        self._is_convolutional = self.model.is_convolutional
        self._keep_time_axis = self.model.is_recurrent
        self._impute_missing = self.model.impute
        self._indices = []
        self._n_sample = ds.dims['sample']
        self._has_time_step = 'time_step' in ds.dims

        self.on_epoch_end()

    @property
    def shape(self):
        """
        :return: the full shape of predictors, (time_step, [variable, level,] lat, lon)
        """
        if self._has_time_step:
            return self.ds.predictors.shape[1:]
        else:
            return (1,) + self.ds.predictors.shape[1:]

    @property
    def n_features(self):
        """
        :return: int: the number of features in the predictor array
        """
        return int(np.prod(self.shape))

    @property
    def dense_shape(self):
        """
        :return: the shape of flattened features. If the model is recurrent, (time_step, features); otherwise,
            (features,).
        """
        if self._keep_time_axis:
            return (self.shape[0],) + (self.n_features // self.shape[0],)
        else:
            return (self.n_features,) + ()

    @property
    def convolution_shape(self):
        """
        :return: the shape of the predictors expected by a Conv2D or ConvLSTM2D layer. If the model is recurrent,
            (time_step, channels, y, x); if not, (channels, y, x).
        """
        if self._keep_time_axis:
            return (self.shape[0],) + (int(np.prod(self.shape[1:-2])),) + self.shape[-2:]
        else:
            return (int(np.prod(self.shape[:-2])),) + self.ds.predictors.shape[-2:]

    @property
    def shape_2d(self):
        """
        :return: the shape of the predictors expected by a Conv2D layer, (channels, y, x)
        """
        if self._keep_time_axis:
            self._keep_time_axis = False
            s = tuple(self.convolution_shape)
            self._keep_time_axis = True
            return s
        else:
            return self.convolution_shape

    def on_epoch_end(self):
        self._indices = np.arange(self._n_sample)
        if self._shuffle:
            np.random.shuffle(self._indices)

    def generate(self, samples, scale_and_impute=True):
        if len(samples) > 0:
            ds = self.ds.isel(sample=samples)
        else:
            ds = self.ds.isel(sample=slice(None))
        n_sample = ds.predictors.shape[0]
        p = ds.predictors.values.reshape((n_sample, -1))
        t = ds.targets.values.reshape((n_sample, -1))
        ds.close()
        ds = None

        # Remove samples with NaN; scale and impute
        if self._remove_nan:
            p, t = delete_nan_samples(p, t)
        if scale_and_impute:
            if self._impute_missing:
                p, t = self.model.imputer_transform(p, t)
            p, t = self.model.scaler_transform(p, t)

        # Format spatial shape for convolutions; also takes care of time axis
        if self._is_convolutional:
            p = p.reshape((n_sample,) + self.convolution_shape)
            t = t.reshape((n_sample,) + self.convolution_shape)
        elif self._keep_time_axis:
            p = p.reshape((n_sample,) + self.dense_shape)
            t = t.reshape((n_sample,) + self.dense_shape)

        return p, t

    def __len__(self):
        """
        :return: the number of batches per epoch
        """
        return int(np.ceil(self._n_sample / self._batch_size))

    def __getitem__(self, index):
        """
        Get one batch of data
        :param index: index of batch
        :return: (ndarray, ndarray): predictors, targets
        """
        # Generate indexes of the batch
        if int(index) < 0:
            index = len(self) + index
        if index > len(self):
            raise IndexError
        indexes = self._indices[index * self._batch_size:(index + 1) * self._batch_size]

        # Generate data
        X, y = self.generate(indexes)

        return X, y


class SmartDataGenerator(Sequence):
    """
    Class used to generate training data on the fly from a loaded DataSet of predictor data. Depends on the structure
    of the EnsembleSelector to do scaling and imputing of data. This particular class loads the dataset efficiently by
    leveraging its knowledge of the predictor-target sequence and time_step dimension. DO NOT USE if the predictors
    and targets are not a continuous time sequence where dt between samples equals dt between time_steps.
    """

    def __init__(self, model, ds, batch_size=32, shuffle=False, remove_nan=True, load=True):
        """
        Initialize a SmartDataGenerator.

        :param model: instance of a DLWP model
        :param ds: xarray Dataset: predictor dataset. Should have attributes 'predictors' and 'targets'
        :param batch_size: int: number of samples to take at a time from the dataset
        :param shuffle: bool: if True, randomly select batches
        :param remove_nan: bool: if True, remove any samples with NaNs
        :param load: bool: if True, load the data in memory
        """
        warnings.warn("SmartDataGenerator is deprecated and may be removed in the future; use "
                      "SeriesDataGenerator instead", DeprecationWarning)
        self.model = model
        if not hasattr(ds, 'predictors'):
            raise ValueError("dataset must have 'predictors' variable")
        self.ds = ds
        self._batch_size = batch_size
        self._shuffle = shuffle
        self._remove_nan = remove_nan
        self._is_convolutional = self.model.is_convolutional
        self._keep_time_axis = self.model.is_recurrent
        self._impute_missing = self.model.impute
        self._indices = []
        self._n_sample = ds.dims['sample']
        if 'time_step' in ds.dims:
            self.time_dim = ds.dims['time_step']
            self.da = self.ds.predictors.isel(time_step=0)
            # Add the last time steps in the series
            self.da = xr.concat((self.da, self.ds.predictors.isel(
                sample=slice(self._n_sample - self.time_dim + 1, None), time_step=-1)), dim='sample')
        else:
            self.time_dim = 1
            self.da = self.ds.predictors

        if hasattr(self.ds, 'targets'):
            self.da = xr.concat((self.da, self.ds.targets.isel(sample=slice(self._n_sample - self.time_dim, None),
                                                               time_step=-1)), dim='sample')
        if load:
            self.da.load()
        else:
            warnings.warn('data for SeriesDataGenerator is not loaded into memory; performance may be very slow')
        self.on_epoch_end()

    @property
    def shape(self):
        """
        :return: the full shape of predictors, (time_step, [variable, level,] lat, lon)
        """
        return (self.time_dim,) + self.da.shape[1:]

    @property
    def n_features(self):
        """
        :return: int: the number of features in the predictor array
        """
        return int(np.prod(self.shape))

    @property
    def dense_shape(self):
        """
        :return: the shape of flattened features. If the model is recurrent, (time_step, features); otherwise,
            (features,).
        """
        if self._keep_time_axis:
            return (self.shape[0],) + (self.n_features // self.shape[0],)
        else:
            return (self.n_features,) + ()

    @property
    def convolution_shape(self):
        """
        :return: the shape of the predictors expected by a Conv2D or ConvLSTM2D layer. If the model is recurrent,
            (time_step, channels, y, x); if not, (channels, y, x).
        """
        if self._keep_time_axis:
            return (self.shape[0],) + (int(np.prod(self.shape[1:-2])),) + self.shape[-2:]
        else:
            return (int(np.prod(self.shape[:-2])),) + self.ds.predictors.shape[-2:]

    @property
    def shape_2d(self):
        """
        :return: the shape of the predictors expected by a Conv2D layer, (channels, y, x)
        """
        if self._keep_time_axis:
            self._keep_time_axis = False
            s = tuple(self.convolution_shape)
            self._keep_time_axis = True
            return s
        else:
            return self.convolution_shape

    def on_epoch_end(self):
        self._indices = np.arange(self._n_sample)
        if self._shuffle:
            np.random.shuffle(self._indices)

    def generate(self, samples, scale_and_impute=True):
        if len(samples) == 0:
            samples = np.arange(self._n_sample, dtype=np.int)
        else:
            samples = np.array(samples, dtype=np.int)
        n_sample = len(samples)
        p = np.concatenate([self.da.values[samples + n, np.newaxis] for n in range(self.time_dim)], axis=1)
        p = p.reshape((n_sample, -1))
        t = np.concatenate([self.da.values[samples + self.time_dim + n, np.newaxis] for n in range(self.time_dim)],
                           axis=1)
        t = t.reshape((n_sample, -1))

        # Remove samples with NaN; scale and impute
        if self._remove_nan:
            p, t = delete_nan_samples(p, t)
        if scale_and_impute:
            if self._impute_missing:
                p, t = self.model.imputer_transform(p, t)
            p, t = self.model.scaler_transform(p, t)

        # Format spatial shape for convolutions; also takes care of time axis
        if self._is_convolutional:
            p = p.reshape((n_sample,) + self.convolution_shape)
            t = t.reshape((n_sample,) + self.convolution_shape)
        elif self._keep_time_axis:
            p = p.reshape((n_sample,) + self.dense_shape)
            t = t.reshape((n_sample,) + self.dense_shape)

        return p, t

    def __len__(self):
        """
        :return: the number of batches per epoch
        """
        return int(np.ceil(self._n_sample / self._batch_size))

    def __getitem__(self, index):
        """
        Get one batch of data
        :param index: index of batch
        :return: (ndarray, ndarray): predictors, targets
        """
        # Generate indexes of the batch
        if int(index) < 0:
            index = len(self) + index
        if index > len(self):
            raise IndexError
        indexes = self._indices[index * self._batch_size:(index + 1) * self._batch_size]

        # Generate data
        X, y = self.generate(indexes)

        return X, y


class SeriesDataGenerator(Sequence):
    """
    Class used to generate training data on the fly from a loaded DataSet of predictor data. Depends on the structure
    of the EnsembleSelector to do scaling and imputing of data. This class expects DataSet to contain a single variable,
    'predictors', which is a continuous time sequence of weather data. The user supplies arguments to load specific
    variables/levels and the number of time steps for the inputs/outputs. It is highly recommended to use the option
    to load the data into memory if enough memory is available as the increased I/O calls for generating the correct
    data sequences will take a toll. This class also makes it possible to add model-invariant data, such as incoming
    solar radiation, to the inputs.
    """

    def __init__(self, model, ds, rank=2, input_sel=None, output_sel=None, input_time_steps=1, output_time_steps=1,
                 sequence=None, interval=1, add_insolation=False, batch_size=32, shuffle=False, remove_nan=True,
                 load='required', force_load=False):
        """
        Initialize a SeriesDataGenerator.

        :param model: instance of a DLWP model
        :param ds: xarray Dataset: predictor dataset. Should have attribute 'predictors'.
        :param rank: int: the number of spatial dimensions (e.g. 2 for 2-d data and convolutions)
        :param input_sel: dict: variable/level selection for input features
        :param output_sel: dict: variable/level selection for output features
        :param input_time_steps: int: number of time steps in the input features
        :param output_time_steps: int: number of time steps in the output features (recommended either 1 or the same
            as input_time_steps)
        :param sequence: int or None: if int, then the output targets is a list of sequence consecutive forecast steps.
            Note that in this mode, if add_insolation is True, the inputs are also a list of consecutive forecast steps,
            with the first step containing all of the input data and subsequent steps containing only the requisite
            insolation fields.
        :param interval: int: the number of steps to take when producing target data. For example, if interval is 2 and
            the spacing between time steps is 6 h, the target will be 12 hours in the future.
        :param add_insolation: bool or str:
            if False: do not add incoming solar radiation
            if True: add insolation to the inputs. Incompatible with 3-d convolutions.
            if 'hourly': same as True
            if 'daily': add the daily max insolation without diurnal cycle
        :param batch_size: int: number of samples to take at a time from the dataset
        :param shuffle: bool: if True, randomly select batches
        :param remove_nan: bool: if True, remove any samples with NaNs
        :param load: str: option for loading data into memory. If it evaluates to negative, no memory loading is done.
            THIS IS LIKELY VERY SLOW.
            'full': load the full dataset. May use a lot of memory.
            'required': load only the required variables, but this also loads two separate datasets for predictors and
                targets
            'minimal': load only one copy of the data, but also loads all of the variables. This may use half as much
                memory as 'required', but only if there are no unused extra variables in the file. Note that in order
                to attempt to use numpy views to save memory, the order of variables may be different from the
                input and output selections.
        :param force_load: if True, load the data upon initialization of instance
        """
        self.model = model
        if not hasattr(ds, 'predictors'):
            raise ValueError("dataset must have 'predictors' variable")
        assert int(rank) > 0
        assert int(input_time_steps) > 0
        assert int(output_time_steps) > 0
        assert int(batch_size) > 0
        assert int(interval) > 0
        if sequence is not None:
            assert int(sequence) > 0
        if not(not load):
            if load not in ['full', 'required', 'minimal']:
                if isinstance(load, bool):
                    load = 'required'
                else:
                    raise ValueError("'load' must be one of 'full', 'required', or 'minimal'")
        try:
            add_insolation = to_bool(add_insolation)
        except ValueError:
            pass
        assert isinstance(add_insolation, (bool, str))
        if isinstance(add_insolation, str):
            assert add_insolation in ['hourly', 'daily']
        self._add_insolation = 1 if isinstance(add_insolation, str) else int(add_insolation)
        self._daily_insolation = str(add_insolation) == 'daily'
        self._load = load
        self._is_loaded = False

        self.ds = ds
        self._batch_size = batch_size
        self._shuffle = shuffle
        self._remove_nan = remove_nan
        self._is_convolutional = self.model.is_convolutional
        self._keep_time_axis = self.model.is_recurrent
        self._impute_missing = self.model.impute
        self._indices = []
        self._sequence = sequence
        if self._sequence is not None:
            self._n_sample = ds.dims['sample'] - input_time_steps - output_time_steps * sequence + 2 - interval
        else:
            self._n_sample = ds.dims['sample'] - input_time_steps - output_time_steps + 2 - interval
        if 'time_step' in ds.dims:
            # Use -1 index because Preprocessor.data_to_samples (which generates a 'time_step' dim), assigns the
            # datetime 'sample' dim based on the initialization time, time_step=-1
            self.da = self.ds.predictors.isel(time_step=-1)
        else:
            self.da = self.ds.predictors

        self.rank = rank
        self._input_sel = input_sel or {}
        if len(self._input_sel) == 0:
            if 'varlev' in self.ds.variables.keys():
                self._input_sel = {'varlev': self.ds['varlev'].values}
            else:
                self._input_sel = {'variable': self.ds['variable'].values, 'level': self.ds['level'].values}
        self._output_sel = output_sel or {}
        if len(self._output_sel) == 0:
            if 'varlev' in self.ds.variables.keys():
                self._output_sel = {'varlev': self.ds['varlev'].values}
            else:
                self._output_sel = {'variable': self.ds['variable'].values, 'level': self.ds['level'].values}
        self._input_time_steps = input_time_steps
        self._output_time_steps = output_time_steps
        self._interval = interval

        # Temporarily set DataArrays for coordinates
        self.input_da = self.da.sel(**self._input_sel)
        self.output_da = self.da.sel(**self._output_sel)
        if force_load:
            self._load_data()

        self.on_epoch_end()

        # Pre-generate the insolation data
        if self._add_insolation:
            sol = insolation(self.da.sample.values, self.ds.lat.values, self.ds.lon.values,
                             daily=self._daily_insolation)
            self.insolation_da = xr.DataArray(sol, dims=['sample'] + ['x%d' % r for r in range(self.rank)])
            self.insolation_da['sample'] = self.da.sample.values

    def _load_data(self):
        if self._load == 'full':
            self.ds.load()
        if self._load == 'minimal':
            # Try to transpose the axes so we can use basic indexing to return views
            if 'varlev' in self._input_sel.keys():
                union = [s for s in self._input_sel['varlev'] if s in self._output_sel['varlev']]
                added_in = [s for s in self._input_sel['varlev'] if s not in union]
                added_out = [s for s in self._output_sel['varlev'] if s not in union]
                if len(added_in) > 0 and len(added_out) > 0:
                    warnings.warn("Found extra variables in both input and output, could not reduce to basic "
                                  "indexing. 'minimal' indexing will use much more memory than 'required'.")
                    self.da.load()
                    self.input_da = self.da.sel(**self._input_sel)
                    self.output_da = self.da.sel(**self._output_sel)
                else:
                    self.da = self.da.sel(varlev=union + added_in + added_out)
                    self.da.load()
                    self.input_da = self.da.isel(varlev=slice(0, len(union) + len(added_in)))
                    self.output_da = self.da.isel(varlev=slice(0, len(union) + len(added_out)))
            else:
                raise NotImplementedError("Check for 'minimal' data loading not implemented yet for input files with "
                                          "variable/level axes. Use 'required' to avoid excessive memory use.")
        else:
            self.input_da = self.da.sel(**self._input_sel)
            self.output_da = self.da.sel(**self._output_sel)
            if self._load == 'required':
                self.input_da.load()
                self.output_da.load()
        self._is_loaded = True

    @property
    def shape(self):
        """
        :return: the original shape of input data: (time_step, [variable, level,] lat, lon); excludes insolation
        """
        return (self._input_time_steps,) + self.input_da.shape[1:]

    @property
    def n_features(self):
        """
        :return: int: the number of input features; includes insolation
        """
        return int(np.prod(self.shape)) + int(np.prod(self.shape[-self.rank:])) \
            * self._input_time_steps * self._add_insolation

    @property
    def dense_shape(self):
        """
        :return: the shape of flattened input features. If the model is recurrent, (time_step, features); otherwise,
            (features,).
        """
        if self._keep_time_axis:
            return (self.shape[0],) + (self.n_features // self.shape[0],)
        else:
            return (self.n_features,) + ()

    @property
    def convolution_shape(self):
        """
        :return: the shape of the predictors expected by a Conv2D or ConvLSTM2D layer. If the model is recurrent,
            (time_step, channels, y, x); if not, (channels, y, x). Includes insolation.
        """
        if self._keep_time_axis:
            return (self._input_time_steps,) + (int(np.prod(self.shape[1:-self.rank])) + self._add_insolation,)\
                + self.shape[-self.rank:]
        else:
            return (int(np.prod(self.shape[:-self.rank])) +
                    self._input_time_steps * self._add_insolation,) + self.input_da.shape[-self.rank:]

    @property
    def shape_2d(self):
        """
        :return: the shape of the predictors expected by a Conv2D layer, (channels, y, x); includes insolation
        """
        if self._keep_time_axis:
            self._keep_time_axis = False
            s = tuple(self.convolution_shape)
            self._keep_time_axis = True
            return s
        else:
            return self.convolution_shape

    @property
    def output_shape(self):
        """
        :return: the original shape of outputs: (time_step, [variable, level,] lat, lon)
        """
        return (self._output_time_steps,) + self.output_da.shape[1:]

    @property
    def output_n_features(self):
        """
        :return: int: the number of output features
        """
        return int(np.prod(self.output_shape))

    @property
    def output_dense_shape(self):
        """
        :return: the shape of flattened output features. If the model is recurrent, (time_step, features); otherwise,
            (features,).
        """
        if self._keep_time_axis:
            return (self.output_shape[0],) + (self.output_n_features // self.output_shape[0],)
        else:
            return (self.output_n_features,) + ()

    @property
    def output_convolution_shape(self):
        """
        :return: the shape of the predictors expected to be returned by a Conv2D or ConvLSTM2D layer. If the model is
            recurrent, (time_step, channels, y, x); if not, (channels, y, x).
        """
        if self._keep_time_axis:
            return (self._output_time_steps,) + (int(np.prod(self.output_shape[1:-self.rank])),) \
                + self.output_shape[-self.rank:]
        else:
            return (int(np.prod(self.output_shape[:-self.rank])),) + self.output_da.shape[-self.rank:]

    @property
    def output_shape_2d(self):
        """
        :return: the shape of the predictors expected to be returned by a Conv2D layer, (channels, y, x)
        """
        if self._keep_time_axis:
            self._keep_time_axis = False
            s = tuple(self.output_convolution_shape)
            self._keep_time_axis = True
            return s
        else:
            return self.output_convolution_shape

    @property
    def insolation_shape(self):
        """
        :return: the shape of insolation inputs in steps 1- of an input sequence, or None if add_insolation is False.
            Note that it always includes the time step dimension, but no channels dimension. The network needs to
            accomodate this.
        """
        return tuple((self._input_time_steps, 1) + self.convolution_shape[-self.rank:])

    def on_epoch_end(self):
        self._indices = np.arange(self._n_sample)
        if self._shuffle:
            np.random.shuffle(self._indices)

    def generate(self, samples, scale_and_impute=True):
        if len(samples) == 0:
            samples = np.arange(self._n_sample, dtype=np.int)
        else:
            samples = np.array(samples, dtype=np.int)
        n_sample = len(samples)

        if not self._is_loaded:
            print('SeriesDataGenerator: loading data to memory')
            self._load_data()

        # Predictors
        p = np.concatenate([self.input_da.values[samples + n, np.newaxis] for n in range(self._input_time_steps)],
                           axis=1)
        if self._add_insolation:
            # Pretend like we have no insolation and keep the time axis
            self._add_insolation = False
            keep_time = bool(self._keep_time_axis)
            self._keep_time_axis = True
            shape = tuple(self.convolution_shape)
            self._add_insolation = True
            self._keep_time_axis = bool(keep_time)
            insol = []
            if self._sequence is not None:
                for s in range(self._sequence):
                    insol.append(
                        np.concatenate([self.insolation_da.values[samples + self._input_time_steps * s + n,
                                                                  np.newaxis, np.newaxis]
                                        for n in range(self._input_time_steps)], axis=1)
                    )
            else:
                insol.append(
                    np.concatenate([self.insolation_da.values[samples + n, np.newaxis, np.newaxis]
                                    for n in range(self._input_time_steps)], axis=1)
                )
            p = p.reshape((n_sample,) + shape)
            p = np.concatenate([p, insol[0]], axis=2)
        p = p.reshape((n_sample, -1))

        # Targets, including sequence if desired
        if self._sequence is not None:
            targets = []
            for s in range(self._sequence):
                t = np.concatenate([self.output_da.values[samples + self._input_time_steps + self._interval - 1 +
                                                          self._output_time_steps * s + n, np.newaxis]
                                    for n in range(self._output_time_steps)], axis=1)

                t = t.reshape((n_sample, -1))

                # Remove samples with NaN; scale and impute
                if self._remove_nan:
                    p, t = delete_nan_samples(p, t)
                if scale_and_impute:
                    if self._impute_missing:
                        p, t = self.model.imputer_transform(p, t)
                    p, t = self.model.scaler_transform(p, t)

                # Format spatial shape for convolutions; also takes care of time axis
                if self._is_convolutional:
                    p = p.reshape((n_sample,) + self.convolution_shape)
                    t = t.reshape((n_sample,) + self.output_convolution_shape)
                elif self._keep_time_axis:
                    p = p.reshape((n_sample,) + self.dense_shape)
                    t = t.reshape((n_sample,) + self.output_dense_shape)

                targets.append(t)

            # Sequence of inputs (plus insolation) for predictors
            if self._add_insolation:
                p = [p] + insol[1:]
        else:
            t = np.concatenate([self.output_da.values[samples + self._input_time_steps + n +
                                                      self._interval - 1, np.newaxis]
                                for n in range(self._output_time_steps)], axis=1)

            t = t.reshape((n_sample, -1))

            # Remove samples with NaN; scale and impute
            if self._remove_nan:
                p, t = delete_nan_samples(p, t)
            if scale_and_impute:
                if self._impute_missing:
                    p, t = self.model.imputer_transform(p, t)
                p, t = self.model.scaler_transform(p, t)

            # Format spatial shape for convolutions; also takes care of time axis
            if self._is_convolutional:
                p = p.reshape((n_sample,) + self.convolution_shape)
                t = t.reshape((n_sample,) + self.output_convolution_shape)
            elif self._keep_time_axis:
                p = p.reshape((n_sample,) + self.dense_shape)
                t = t.reshape((n_sample,) + self.output_dense_shape)

            targets = t

        return p, targets

    def __len__(self):
        """
        :return: the number of batches per epoch
        """
        return int(np.ceil(self._n_sample / self._batch_size))

    def __getitem__(self, index):
        """
        Get one batch of data
        :param index: index of batch
        :return: (ndarray, ndarray): predictors, targets
        """
        # Generate indexes of the batch
        if int(index) < 0:
            index = len(self) + index
        if index > len(self):
            raise IndexError
        indexes = self._indices[index * self._batch_size:(index + 1) * self._batch_size]

        # Generate data
        X, y = self.generate(indexes)

        return X, y
