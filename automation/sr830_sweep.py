import logging
import sys
from os import path

import numpy as np

from pymeasure.log import console_log
from pymeasure.adapters import SerialAdapter
from pymeasure.display.Qt import QtGui, QtCore
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results
from pymeasure.experiment import IntegerParameter, FloatParameter
from pymeasure.experiment import unique_filename

from pymeasure.instruments.srs import SR830
from sr830_freq import Sr830ConfigureMixin, AcFreqProcedure

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

class AcSweepProcedure(Procedure, Sr830ConfigureMixin):
    _resource = None
    _is_serial = None

    fmin = FloatParameter('Start frequency', units='Hz',
        minimum=0.001, maximum=102000.0, default=0.050)
    fmax = FloatParameter('Stop frequency', units='Hz',
        minimum=0.001, maximum=102000.0, default=10000)
    points_per_decade = IntegerParameter('Points per decade',
        minimum=1, maximum=100, default=6)
    harmonic = IntegerParameter(
        'Harmonic', minimum=1, maximum=1000000, default=1)
    phase = FloatParameter(
        'Phase', units='°', minimum=-180.0, maximum=+180.0, default=0)
    tolerance = FloatParameter(
        "Tolerance", minimum=0, maximum=1, default=0.01)
    window = IntegerParameter(
        "TestWindow", units="x100 samples", minimum=1, maximum=20)
    filename = 'data_ac.csv'
    
    DATA_COLUMNS = ['Frequency (Hz)', 'Magnitude (VRMS)', 'Gain (dB)', 'Phase (deg)', 'Residual (%)']
    INPUT_VOLTAGE = 0.01


    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._resource is None or self._is_serial is None:
            raise RuntimeError("Must call {}.configure() first".
                format(self.__class__))


    def startup(self):
        AcFreqProcedure.configure(self._resource, self._is_serial)


    def execute(self):
        num_points = int(np.log10(self.fmax/self.fmin) + self.points_per_decade)
        freqs = np.geomspace(self.fmax, self.fmin, num_points)

        for freq in freqs:
            log.info("Measuring @ frequency: {:.3f}".format(freq))
            r, phi, dev = execute_single_freq(freq)

            self.emit('results', {
                'Frequency (Hz)': freq,
                'Magnitude (VRMS)': r,
                'Gain (dB)': 20*np.log10(r/self.INPUT_VOLTAGE),
                'Phase (deg)': phi,
                'Residual (%)': dev
            })

            if self.should_stop():
                log.info("User aborted the procedure")
                break


    def execute_single_freq(self, freq):
        log.info('Measuring at frequency %.3f' % freq)
        procedure = AcFreqProcedure()
        procedure.frequency = freq
        procedure.harmonic = self.harmonic
        procedure.phase = self.phase
        procedure.tolerance = self.tolerance
        procedure.window = self.window
        procedure.generate_auto_parameters()

        data_filename = unique_filename(
            path.splitext(self.filename)[0], prefix='data_freq')
        log.debug("Constructing the Results with a data file: %s" % data_filename)
        results = Results(procedure, data_filename)

        log.debug("Constructing the Worker")
        worker = Worker(results)
        worker.start()
        log.debug("Started the Worker")

        log.debug("Joining with the worker in at most {:f}s".format(self.auto_timeout))
        worker.join(timeout=self.auto_timeout)
        log.debug("Finished the measurement")

        last_data = results.data[-1]
        r = last_data[AcFreqProcedure.COL_RS]
        theta = last_data[AcFreqProcedure.COL_THETAS]
        dev = last_data[AcFreqProcedure.COL_DEV]
        return r, theta, dev


    def shutdown(self):
        self.lia.shutdown()
        log.info("Finished measuring")


class SweepWindow(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=AcSweepProcedure,
            inputs=['fmin', 'fmax', 'points_per_decade', 'harmonic', 'phase',
                    'tolerance', 'window'],
            displays=['fmin', 'fmax', 'points_per_decade', 'harmonic', 'phase',
                      'tolerance', 'window'],
            x_axis='Frequency (Hz)',
            y_axis='Magnitude (dB)'
        )
        self.setWindowTitle('AC Sweep Measurement')

    def queue(self):
        filename = unique_filename('.', prefix="data_ac")

        procedure = self.make_procedure()
        procedure.filename = filename
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


if __name__ == "__main__":
    adapter = SerialAdapter('/dev/ttyUSB0', baudrate=9600, rtscts=True, timeout=5)
    AcSweepProcedure.configure(adapter, True)
    app = QtGui.QApplication(sys.argv)
    app.setStyle("plastique")
    window = SweepWindow()
    window.show()
    sys.exit(app.exec_())
