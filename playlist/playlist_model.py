from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex

class PlaylistModel(QAbstractTableModel):
    """
    Model stores rows as tuples:
      (index_str, title, artist, album, time_str)
    The view uses 4 columns (index, title+artist, album, time).
    """

    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = list(rows or [])

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        # We expose 4 columns: index | name (title+artist) | album | time
        return 4

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        row = self._rows[r]
        # row format: (idx, title, artist, album, time)
        if role == Qt.DisplayRole:
            if c == 0:
                return row[0]
            if c == 1:
                # We'll return a tuple for custom painting (title, artist)
                return (row[1], row[2])
            if c == 2:
                return row[3]
            if c == 3:
                return row[4]
        if role == Qt.TextAlignmentRole:
            if c == 0 or c == 3:
                return Qt.AlignCenter
            return Qt.AlignVCenter | Qt.AlignLeft
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        # Hide headers in view, so simple behavior is fine
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            names = ["#", "Title/Artist", "Album", "Time"]
            return names[section] if section < len(names) else ""
        return super().headerData(section, orientation, role)

    # Convenience
    def add_rows(self, rows):
        start = self.rowCount()
        end = start + len(rows) - 1
        if start <= end:
            self.beginInsertRows(QModelIndex(), start, end)
            self._rows.extend(rows)
            self.endInsertRows()

    def remove_row(self, row):
        if 0 <= row < self.rowCount():
            self.beginRemoveRows(QModelIndex(), row, row)
            self._rows.pop(row)
            self.endRemoveRows()
