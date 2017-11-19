import logging
import sys
from os import path
import argparse

import numpy as np
import time

# Let's prefer PyQt5, if available
# PyMeasure/PyQtGraph will use an already-imported module
try:
    import PyQt5
except ImportError:
    pass

from pymeasure.log import console_log
from pymeasure.adapters import SerialAdapter
from pymeasure.display.Qt import QtGui, QtCore
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, Worker
from pymeasure.experiment import IntegerParameter, FloatParameter
from pymeasure.experiment import unique_filename

from pymeasure.instruments.srs import SR830
from sr830_freq import Sr830ConfigureMixin, AcFreqProcedure

from pymeasure.instruments.srs import SR830, FakeSR830Adapter, FakeSR830DUT
from pymeasure.adapters import FakeScpiAdapter

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
        "Tolerance", units='%', minimum=0, maximum=100, default=1)
    tau_window = IntegerParameter(
        "TestWindow", units="tau", minimum=1, maximum=100, default=3)
    filename = 'data_ac.csv'
    
    COL_F = 'Frequency (Hz)'
    COL_R = 'Magnitude (VRMS)'
    COL_G = 'Gain (dB)'
    COL_THETA = 'Phase (deg)'
    COL_DEV = 'Residual (%)'
    DATA_COLUMNS = [COL_F, COL_R, COL_G, COL_THETA, COL_DEV]

    INPUT_VOLTAGE = 0.01


    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._resource is None or self._is_serial is None:
            raise RuntimeError("Must call {}.configure() first".
                format(self.__class__))


    def startup(self):
        log.info("Parameters: {}".format(self.parameter_values()))
        AcFreqProcedure.configure(self._resource, self._is_serial)
        self.do_reset = True


    def execute(self):
        if self.fmin > self.fmax:
            fmax, fmin = fmin, fmax
        num_points = int(np.log10(self.fmax/self.fmin) * self.points_per_decade)
        freqs = np.geomspace(self.fmax, self.fmin, num_points)

        for freq in freqs:
            log.info("Measuring @ frequency: {:.3f}".format(freq))
            r, phi, dev = self.execute_single_freq(freq)
            self.do_reset = False # no need to full reset on subsequent points

            self.emit('results', {
                self.COL_F: freq,
                self.COL_R: r,
                self.COL_G: 20*np.log10(r/self.INPUT_VOLTAGE),
                self.COL_THETA: phi,
                self.COL_DEV: dev
            })

            if self.should_stop():
                log.info("User aborted the procedure")
                break


    def execute_single_freq(self, freq):
        procedure = AcFreqProcedure(do_reset=self.do_reset)
        procedure.frequency = freq
        procedure.harmonic = self.harmonic
        procedure.phase = self.phase
        procedure.tolerance = self.tolerance
        procedure.tau_window = self.tau_window
        procedure.generate_auto_parameters()

        data_filename = unique_filename(
            path.splitext(self.filename)[0], prefix='data_freq')
        log.debug("Constructing Results. Data file: %s" % data_filename)
        results = Results(procedure, data_filename)

        log.debug("Constructing and starting worker...")
        worker = Worker(results)
        worker.start()

        log.debug("Waiting for task completion...")
        while worker.is_alive():
            worker.join(timeout=100)

            if self.should_stop():
                log.info("User aborted the procedure; stopping subprocedure...")
                worker.stop()
                worker.join()

        last_data = results.data[-1:]
        r = float(last_data[AcFreqProcedure.COL_RS])
        theta = float(last_data[AcFreqProcedure.COL_THETAS])
        dev = float(last_data[AcFreqProcedure.COL_DEV])
        return r, theta, dev


    def shutdown(self):
        log.info("Finished AC Sweep")


class SweepWindow(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=AcSweepProcedure,
            inputs=['fmin', 'fmax', 'points_per_decade', 'harmonic', 'phase',
                    'tolerance', 'tau_window'],
            displays=['fmin', 'fmax', 'points_per_decade', 'harmonic', 'phase',
                      'tolerance', 'tau_window'],
            x_axis=AcSweepProcedure.COL_F,
            y_axis=AcSweepProcedure.COL_G
        )
        self.setWindowTitle('AC Sweep Measurement')

    def queue(self):
        filename = unique_filename('.', prefix="data_ac")

        procedure = self.make_procedure()
        procedure.filename = filename
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)

    def setup_plot(self, plot):
        plot.setLogMode(x=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='AC Single-Frequency Measurement')
    parser.add_argument('--simulate', '-s', default=False, action='store_true')
    args = parser.parse_args()

    if not args.simulate:
        adapter = SerialAdapter('/dev/ttyUSB0', baudrate=9600, rtscts=True, timeout=5)
    else:
        dut = FakeSR830DUT(50e-3, 10000)
        adapter = FakeSR830Adapter(dut)

    log.setLevel(logging.DEBUG)
    console_log(log, level=logging.DEBUG)

    AcSweepProcedure.configure(adapter, True)
    app = QtGui.QApplication(sys.argv)
    app.setStyle("plastique")
    window = SweepWindow()
    window.show()
    sys.exit(app.exec_())
