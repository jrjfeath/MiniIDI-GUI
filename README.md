# MiniIDI - GUI
This UI was made for photonic science miniIDI and miniRXFDI cameras (~2010). 
The UI uses 64-bit photonic science FDIminilabview.dll and fdiminicamlinkcontrol.dll, please reach out to photonic science if you do not have these files.
For the camera to operate the PSL_camera_files folder must be in the same folder as the CamViewer.py

This UI was coded using a pleora framegrabber, others may be added but I have none available for testing.
The pleora dll files are specified in the system path on line 22, it may be easy enough to specify the directory containing the dll for an alternate frame grabber if required.

The camera subarea is defined by xlim and ylim in the sunarea_changed function. Both cameras I have use 1390 * 1040, if yours does not match please change these values.

Has the following requirements:
- python 3.9+
- numpy
- matplotlib
- PyQt6
- ctypes
- h5py
- pillow

If you encounter the following error, update pillow to at least version 9:
"fromImage(image: QImage, flags: Qt.ImageConversionFlag = Qt.AutoColor): argument 1 has unexpected type 'PySide6.QtGui.QImage'"

If the IP engine selection fails with a timeout this is most likely due to the camera not being properly connected or a damaged cable.

To Add:
- Saving user preferences
- Option to save frame by frame