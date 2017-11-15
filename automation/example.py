import logging
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Let's prefer PyQt5, if available
# PyMeasure/PyQtGraph will use an already-imported module
try:
    import PyQt5
except ImportError:
    pass

import sys, os, os.path
import tempfile
import random
from time import sleep
from pymeasure.log import setup_logging
from pymeasure.display.Qt import QtGui, QtCore
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results, Worker
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter

class RandomProcedure(Procedure):

    iterations = IntegerParameter('Loop Iterations')
    delay = FloatParameter('Delay Time', units='s', default=0.2)
    seed = Parameter('Random Seed', default='12345')

    DATA_COLUMNS = ['Iteration', 'Random Number']

    def startup(self):
        log.info("Setting the seed of the random number generator")
        random.seed(self.seed)

    def execute(self):
        log.info("Starting the loop of %d iterations" % self.iterations)
        for i in range(self.iterations):
            data = {
                'Iteration': i,
                'Random Number': random.random()
            }
            self.emit('results', data)
            log.debug("Emitting results: %s" % data)
            sleep(self.delay)
            if self.should_stop():
                log.warning("Caught the stop flag in the procedure")
                break


class MainWindow(ManagedWindow):

    def __init__(self):
        super(MainWindow, self).__init__(
            procedure_class=RandomProcedure,
            inputs=['iterations', 'delay', 'seed'],
            displays=['iterations', 'delay', 'seed'],
            x_axis='Iteration',
            y_axis='Random Number'
        )
        self.setWindowTitle('GUI Example')

    def queue(self):
        filename = tempfile.mktemp()

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


if __name__ == "__main__":
    setup_logging(console=True, console_level='DEBUG')
    log.info("Qt Version: " + QtCore.qVersion())
    # data_filename = 'example.csv'
    # try:
    #     os.remove(data_filename)
    # except OSError as e:
    #     if os.path.exists(data_filename):
    #         raise e # file still exists, couldn't delete - raise error
    #     # else fine - file doesn't exist

    # procedure = RandomProcedure()
    # procedure.iterations = 20
    # procedure.delay = 0.001
    # procedure.seed = os.urandom(16)

    # log.info("Constructing the Results with a data file: %s" % data_filename)
    # results = Results(procedure, data_filename)

    # log.info("Constructing the Worker")
    # worker = Worker(results)
    # worker.start()
    # log.info("Started the Worker")

    # log.info("Joining with the worker in at most 1 min")
    # worker.join(timeout=60)
    # log.info("Finished the measurement")

    # data = results.data
    # log.info("0: " + str(data.mean(axis=0)))
    # log.info("1: " + str(data.mean(axis=1)))
    # log.info("Columns: " + str(data.columns))
    # log.info("Results: " + str(results.data[-10:]))
    # log.info('a ' + str(data[-1:]))

    # sys.exit(0)

    app = QtGui.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

