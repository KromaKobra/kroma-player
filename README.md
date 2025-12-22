# Kroma Player
Kroma Player is a highly customizable and capable music player for online and
downloaded music. It utilizes the Spotify API for finding songs online, and
allows you to create playlists from these and downloaded music. The player also
uses a simple queue designer so you can quickly setup all the music you want to
listen to and then sit back without distractions. The UI is built from arrangable
and poppable docks as well as extensive button settings so you can choose exactly
how feature rich or minimal your player is. Customize it to fit the design of the
rest of your desktop applications, or import a preconfigured design you like!

## How it works
###### This section is meant for those who are trying to understand the code itself.
This application is built with the Qt Framework and Python through PySide6.
### <u>The UI</u>
In *main.py*:
* A QApplication called app is created. All Qt projects need one QApplication
as the main widget that collects mouse inputs and such.
* A QWidget is created and shown as the central widget containing the entire UI of
the player. It is created as the custom class AppWindow which is a child of
QWidget. Inside of this widget a QHBoxLayout is created with margins to add space
between the boarders of the window and the contents of the application.
* The AppWindow then creates an instance of MainWindow (child of QMainWindow) and
places that within its layout. The MainWindow is used to contain all of the pane
docks. MainWindow then adds a temporary QWidget that fills all the space that the
docks don't end up needing. (Eventually the docks will fill the entire window).
Then the MainWindow creates two FramelessDock's for music controllers. The custom
class FramelessDock is a child of QDockWidgets and they are custom docks without
frames and have special systems for docking and popping out. The FramelessDock
class is defined in widgets/frameless_dock.py. The dock widget then has its
contents set to the ControllerPane class that inherits from QWidget and is defined
in panes/controller_pane.py.
* The AppWindow then creates an instance of BorderOverlay which is a custom QWidget
class. It is a boarder for the entire app and is defined in widgets/app_boarder.py.

In *widgets/frameless_dock.py*:
