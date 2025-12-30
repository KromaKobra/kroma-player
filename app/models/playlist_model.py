from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex

class PlaylistModel(QAbstractTableModel):
    HEADERS = ["Title", "Artist", "Album", "Duration"]

    def __init__(self, songs=None):
        super().__init__()
        self._songs = songs or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._songs)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None

        song = self._songs[index.row()]
        return song[index.column()]

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None
