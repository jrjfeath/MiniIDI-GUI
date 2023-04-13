import os
import queue
import sys
import threading
import time

import ctypes
import h5py
import matplotlib.pyplot as plt
import numpy as np

from PIL import Image, ImageQt
from PyQt6 import uic, QtWidgets, QtCore, QtGui

#Directory containing this file and the PSL_camera_files
fd = os.path.dirname(__file__)
#filename for the ui file
uifn = "image.ui"
#create path to ui file
uifp = os.path.join(fd,uifn)
#Directory containing dlls needed to run the camera for Pleora framegrabbers
try: os.add_dll_directory(r"C:\Program Files\Common Files\Pleora")
except FileNotFoundError: print('Pleora files not found.')
#Load the labview controller
labview = ctypes.cdll.LoadLibrary(f'{fd}/FDIminilabview.dll')
#Load the camera controller
fdimini = ctypes.cdll.LoadLibrary(f'{fd}/fdiminicamlinkcontrol.dll')
#Some functions that we grab from the labview dll, easier to work with
close_cam = getattr(labview, '?Close@@YA_NXZ')
snap = getattr(labview, '?Snap@@YA_NXZ')
get_array = getattr(labview, '?Get_Image_Data_into_2d_Array@@YA_NPEAG@Z')

class ImageAcquisitionThread(threading.Thread):
    '''
    This thread handles grabbing frames from the camera.
    snap grabs an image and holds it in memory.
    get_array gets the snap from memory and writes it to the numpy ctypes array.
    '''
    def __init__(self):
        super(ImageAcquisitionThread, self).__init__()
        self._running = True
        self._image_queue = queue.Queue(maxsize=2)

    def get_output_queue(self):
        return self._image_queue

    def stop(self):
        self._running = False

    def run(self):
        #Get the width and height for setting up the arrays
        width = fdimini.PSL_VHR_Return_width()
        height = fdimini.PSL_VHR_Return_height()
        while self._running:
            try:
                a = np.zeros((height,width),dtype=np.int16)
                #Create a C pointer to use when writing the data to the numpy array
                b = a.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
                snap()
                get_array(b)
                #Pass the array to the image queue thread for processing
                self._image_queue.put_nowait(a)
            except queue.Full:
                # No point in keeping this image around when the queue is full, let's skip to the next one
                pass
            except Exception as error:
                print("Encountered error: {error}, image acquisition will stop.".format(error=error))
                break
        print("Image acquisition has stopped")

class run_camera(QtCore.QObject):
    '''
    This is the class responsible for updating and saving the image
    When this worker completes its task it signals to the main thread
    Pass the window as a variable so it can update the image
    '''
    finished = QtCore.pyqtSignal()

    def __init__(self, window):
        super(run_camera, self).__init__()
        self._isRunning = False
        self._window = window

    def save_data(self,filename,array):
        with h5py.File(filename, 'w') as hf:
            hf.create_dataset('Intensity', 
                data=array, 
                compression="gzip", 
                chunks=True,
                maxshape=(array.shape)
            )

    def task(self):
        if not self._isRunning:
            self._isRunning = True
        
        print('Starting camera.')
        #Make a worker for the thread (QObject)
        image_acquisition_thread = ImageAcquisitionThread()
        #Make a queue to store image data
        image_queue = image_acquisition_thread.get_output_queue()
        #Start taking pictures after camera setup
        image_acquisition_thread.start()

        #Get the width and height of the camera
        width = fdimini.PSL_VHR_Return_width()
        height = fdimini.PSL_VHR_Return_height()

        #The camera doesnt round very nicely, set to min value
        if width < height:
            height = width
        elif height < width:
            width = height

        #Create a filename for saving data
        filename = os.path.join(self._window._dir_name.text(),self._window._file_name.text())
        filename += '_0000.h5'

        #Check if file already exists so you dont overwrite data
        fid = 1
        while os.path.exists(filename):
            filename = f'{filename[:-8]}_{"{:04d}".format(fid)}.h5'
            fid+=1

        #Variables to keep track of fps and averaging
        shot_number = 1
        fps = 0
        start = time.time()
        save_timer = time.time()

        #Cumulative image array
        cml_image = np.zeros((height,width),dtype=np.int16)
        shot_image = np.zeros((height,width),dtype=np.int16)

        #If the user is saving data write the empty array to the file
        if self._window._save_box.isChecked():
            self.save_data(filename,cml_image)

        #Continue checking the image queue until we kill the camera.
        while self._isRunning == True:
            #Save data every 30 seconds to reduce disk writes
            if time.time() - save_timer > 30:
                if self._window._save_box.isChecked():
                    self.save_data(filename,cml_image)
                save_timer = time.time()

            #If the user wants to refresh the cumulative image clear array
            if self._window._reset_cml == True:
                cml_image = np.zeros((height,width),dtype=np.int16)
                shot_image = np.zeros((height,width),dtype=np.int16)
                self._window._reset_cml = False        

            #Update the fps count every second
            if time.time() - start > 1:
                self._window._fps.setText(f'{fps} fps')
                start = time.time()
                fps = 0

            #If queue is empty the program will crash if you try to check it
            if image_queue.empty():
                pass
            else:
                fps+=1
                #Determine colourmap
                cm_text = self._window._colourmap.currentText().lower()
                if cm_text == 'none':
                    cm = plt.get_cmap('gray')
                else:
                    cm = plt.get_cmap(cm_text)
                
                #Get image array from queue
                image = image_queue.get_nowait()
                #Make sure the image is square
                image = image[0:height,0:width]
                image -= np.min(image) 
                # #Remove values outside of threshold
                max_p = np.max(image)
                image_p = ((image / max_p) * 100)
                image[image_p < self._window._min_intensity.value()] = 0
                image[image_p > self._window._max_intensity.value()] = 255
                #Rotate the image by the specified angle
                image = np.rot90(image, self._window._rotation)
                
                #Write the image to the array
                if shot_number == 1:
                    cml_image = image
                    shot_image = image
                else:
                    #Write a weighted cumulative image to prevent numbers from getting too large
                    cml_image = ((cml_image  * (shot_number - 1)) / shot_number) + (image / shot_number)
                    #Shot on and shot off image for background subtraction
                    if shot_number % 2 == 0:
                        shot_image -= image
                    else:
                        shot_image += image
                shot_number+=1

                #If the user wants to look at a cumulative image or shot on and shot off
                if self._window._view.currentIndex() == 1:
                    image = (cml_image * (255.0 / cml_image.max())).astype(np.uint16)
                elif self._window._view.currentIndex() == 2:
                    image = (shot_image * (255.0 / cml_image.max())).astype(np.uint16)
                
                #Create an RGB image from the array
                colour_image = (cm(image)[:,:,:3] * 255).astype(np.uint8)

                #Make an image from the array
                im = Image.fromarray(colour_image)
                #convert image into pyqt format
                qim = ImageQt.ImageQt(im.convert('RGB'))
                pixmap = QtGui.QPixmap.fromImage(qim)
                scaled_pixmap = pixmap.scaled(
                    self._window._camera.size(),
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.FastTransformation
                    )
                self._window._camera.setPixmap(scaled_pixmap)
        
        image_acquisition_thread.stop()
        image_acquisition_thread.join()

        #Write any data that might not have been stored yet
        if self._window._save_box.isChecked():
            self.save_data(filename,cml_image)

        self.finished.emit()
        print('Camera stopping.')

    def stop(self):
        self._isRunning = False

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        #Load the ui file
        uic.loadUi(uifp,self)

        self._rotation = 0 # Rotation angle of the image

        #Check if camera is connected
        self._connected = self.check_cameras(f'{fd}\PSL_camera_files',)
        #Set flag to false for camera running
        self._camera_running = False
        #Flag to reset cumulative images
        self._reset_cml = False
        self._dir_name.setText(os.path.expanduser('~\\Documents'))

        self._button.clicked.connect(self.camera_control)
        self._path_button.clicked.connect(self.open_file_dialog)
        self._rotate_c.clicked.connect(lambda: self.rotate_camera(0))
        self._rotate_cc.clicked.connect(lambda: self.rotate_camera(1))
        self._update_camera.clicked.connect(self.update_camera)
        self._reset_images.clicked.connect(self.reset_images)

        #If the user goes of the limits for the camera change limits
        #so that the camera doesnt softlock and require a restart
        self._ori_x.valueChanged.connect(lambda: self.subarea_changed(0))
        self._end_x.valueChanged.connect(lambda: self.subarea_changed(1))
        self._ori_y.valueChanged.connect(lambda: self.subarea_changed(2))
        self._end_y.valueChanged.connect(lambda: self.subarea_changed(3))

        self._button.setDisabled(True)
        #self._save_setup.triggered.connect(self.save_setup)
        #self.load_setup()

    def subarea_changed(self,value):
        '''
        This function controls the subarea for the camera.

        The camera has a maximum resolution of 1390 by 1040 anything
        above those limits softlocks the camera, requiring a restart.
        '''
        ox = self._ori_x.value()
        ex = self._end_x.value()
        oy = self._ori_y.value()
        ey = self._end_y.value()
        x_lim = 1390
        y_lim = 1040
        #Prevent the user from setting the origin to be within 10 pixels of end
        if value == 0:
            if ex - ox < 10:
                self._ori_x.setValue(ox - 1)
        if value == 1:
            if ex - ox < 10:
                self._end_x.setValue(ox + 10)
            if (ex > x_lim) and (ex - ox > x_lim):
                self._ori_x.setValue(ex - x_lim)
        if value == 2:
            if ey - oy < 10:
                self._ori_y.setValue(oy - 1)
        if value == 3:
            if ey - oy < 10:
                self._end_y.setValue(oy + 10)
            if (ey > y_lim) and (ey - oy > y_lim):
                self._ori_y.setValue(ey - y_lim)

    def check_cameras(self,dir_):
        #If camera is connected it returns 0, else returns 1
        c_s = ctypes.c_char_p(bytes(dir_,'utf-8'))
        connected = fdimini.PSL_VHR_Init(c_s)
        if connected == 1:
            self._camera_connect.setText(f'Camera disconnected')
            self._camera_connect.setStyleSheet("color: red")
            return False
        self._camera_connect.setText(f'Connected to Camera')
        self._camera_connect.setStyleSheet("color: green")
        return True

    def open_file_dialog(self):
        dirname = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Directory")
        if dirname:
            self._dir_name.setText(dirname)

    def change_interaction(self,value=False):
        '''
        When the camera is running disable any values that dont update.
        '''
        self._ori_x.setDisabled(value)
        self._ori_y.setDisabled(value)
        self._end_x.setDisabled(value)
        self._end_y.setDisabled(value)
        self._binx.setDisabled(value)
        self._biny.setDisabled(value)
        self._exposure.setDisabled(value)
        self._intensifier.setDisabled(value)
        self._gain.setDisabled(value)
        self._trigger.setDisabled(value)
        self._path_button.setDisabled(value)
        self._save_box.setDisabled(value)
        self._update_camera.setDisabled(value)

    def rotate_camera(self,option):
        #Rotate clockwise
        if option == 0:
            self._rotation += 1
            if self._rotation == 4: self._rotation = 0
        #Rotate counter clockwise
        else:
            self._rotation -= 1
            if self._rotation < 0: self._rotation = 3
        self._label_rotation.setText(f'{self._rotation * 90}')

    def reset_images(self):
        if self._reset_cml == False:
            self._reset_cml = True

    '''
    def save_setup(self):
        setup_file = os.path.join(fd,'setup.json')
        setup = {
            '_ori_x' : self._ori_x.value(),
            '_ori_y' : self._ori_y.value(),
            '_binx' : self._binx.value(),
            '_biny' : self._biny.value(),
            '_width' : self._width.value(),
            '_height' : self._height.value(),
            '_exposure' : self._exposure.value(),
            '_minimum' : self._minimum.value(),
            '_maximum' : self._maximum.value(),
            '_rotation' : self._rotation,
            '_colourmap' : self._colourmap.currentText(),
            '_trigger' : self._trigger.currentText(),
            '_trigger_delay' : self._trigger_delay.value()
        }
        with open(setup_file, 'w', encoding='utf-8') as f:
            json.dump(setup, f, ensure_ascii=False, indent=4)  

    def load_setup(self):
        #Check if user has saved settings, if so load them.
        setup_file = os.path.join(fd,'setup.json')
        if os.path.isfile(setup_file) == True:
            with open(setup_file, 'r') as f:
                setup = json.load(f)
                for key in setup:
                    print(getattr(self, key).__class__.__name__)
                    try: getattr(self, key).setValue(setup[key])
                    except AttributeError: print(key)
    '''

    def update_camera(self):
        #Disable the run button until parameters are updated
        self._button.setDisabled(True)
         #Mode 1 software, Mode 6 hardware
        trig_mode = 0
        if self._trigger.currentIndex() == 0:
            trig_mode = 6
        print('trigger ',fdimini.PSL_VHR_SetTriggerMode(trig_mode))
        #Set gains on camera
        print('video gain ',fdimini.PSL_VHR_WriteVideoGain(self._gain.value()))
        print('intensifier ',fdimini.PSL_VHR_WriteIntensifierGain(self._intensifier.value()))
        #Camera exposure time
        print('exposure ',fdimini.PSL_VHR_WriteExposure(0,self._exposure.value()))
        #Camera refresh rate, non-zero success
        print('readout ',fdimini.PSL_VHR_WriteSpeed(1))
        #Set subarea and binning
        print(fdimini.PSL_VHR_Set_subarea_and_binning(
            self._ori_x.value(), self._ori_y.value(), self._end_x.value(),
            self._end_y.value(), self._binx.value(), self._biny.value()
        ))
        #These commands are possible but not used in the labview
        #Pixel offset
        #fdimini.PSL_VHR_enable_offset_subtraction(param['offset'])
        #Bright pixel
        #fdimini.PSL_VHR_enable_dark_field_subtraction(param['bright pixel'])
        #Bright corner
        #fdimini.PSL_VHR_enable_bright_corner_subtraction(param['bright corner'])
        #Flat field
        #fdimini.PSL_VHR_enable_flat_field_subtraction(param['flat'])

        #Enable the run button again
        self._button.setDisabled(False)
        print('Done setting up camera.')

    def camera_control(self):
        '''
        Controls the camera thread.

        Works by creating a pyqt thread object and passing that object a worker.

        When the worker terminates it passes a finished signal which terminates the thread.
        '''
        def set_false(self):
            # Set all values to false when user stops reading data
            self._worker._isRunning = False
            self._camera_running = False
            self._button.setText("Start")

        if self._camera_running == False:
            #Disable camera options so user doesnt get confused
            self.change_interaction(True)
            self._camera_running = True
            self._button.setText("Stop")
            # Step 1: Create a QThread object
            self._thread = QtCore.QThread()
            # Step 2: Create a worker object
            self._worker = run_camera(self)
            # Step 3: Move worker to the thread
            self._worker.moveToThread(self._thread)
            # Step 4: Connect signals and slots
            self._thread.started.connect(self._worker.task)
            self._worker.finished.connect(self._thread.quit)
            # Step 5: Start the thread
            self._thread.start()
            # Step 6: Restore initial values so camera can run again
            self._thread.finished.connect(
                lambda: set_false(self)
            )
        else:
            self._worker._isRunning = False
            #Re-enable camera options for user
            self.change_interaction()

    def closeEvent(self, event):
        '''
        This function captures the close event and overwrites it so we can disconnect
        from the camera before closing down the app.
        '''
        close = QtWidgets.QMessageBox()
        close.setText("Would you like to quit?")
        close.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes | 
                                    QtWidgets.QMessageBox.StandardButton.No)
        close = close.exec()

        if close == QtWidgets.QMessageBox.StandardButton.Yes.value:
            print('Closing')
            #If camera is connected disconnect it before closing
            if self._connected == True:
                print(close_cam())
            event.accept()
        else:
            event.ignore()

def except_hook(cls, exception, traceback):
    #Lets python return errors from pyQt6
    sys.__excepthook__(cls,exception, traceback)

if __name__ == '__main__':
    sys.excepthook = except_hook
    app = QtWidgets.QApplication(sys.argv)
    #Create window
    w = MainWindow()
    #Load the app
    w.show()
    app.exec()
