from PyQt6.QtWidgets import QMessageBox, QLineEdit

def show_info(parent, title, text):
    """Show an information message box."""
    QMessageBox.information(parent, title, text)

def show_warning(parent, title, text):
    """Show a warning message box."""
    QMessageBox.warning(parent, title, text)

def show_error(parent, title, text):
    """Show an error message box."""
    QMessageBox.critical(parent, title, text)

def confirm_action(parent, title, text):
    """
    Show a confirmation message box with Yes and No buttons.
    Returns True if the user clicks Yes, otherwise False.
    """
    reply = QMessageBox.question(
        parent, 
        title, 
        text, 
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return reply == QMessageBox.StandardButton.Yes


def eye_icon(slashed=False, size=20, color=None):
    """Draw a clean vector eye / eye-off icon (outline + pupil, optional
    diagonal slash) for password show/hide toggles. Shared across the
    login screen and admin dashboard so both use the same look."""
    from PyQt6.QtGui import QPixmap, QPainter, QPen, QPainterPath, QIcon, QColor
    from PyQt6.QtCore import Qt, QPointF

    if color is None:
        from styles import TEXT_MUTED
        color = TEXT_MUTED

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    m = size * 0.14
    cx, cy = size / 2, size / 2
    w = size - 2 * m

    path = QPainterPath()
    path.moveTo(m, cy)
    path.cubicTo(cx - w * 0.18, cy - w * 0.34, cx + w * 0.18, cy - w * 0.34, size - m, cy)
    path.cubicTo(cx + w * 0.18, cy + w * 0.34, cx - w * 0.18, cy + w * 0.34, m, cy)
    p.drawPath(path)

    r = size * 0.13
    p.drawEllipse(QPointF(cx, cy), r, r)

    if slashed:
        p.drawLine(QPointF(size * 0.16, size * 0.16), QPointF(size * 0.84, size * 0.84))

    p.end()
    return QIcon(pm)


def make_password_field(placeholder, height=42):
    """QLineEdit that starts masked, with a trailing eye icon to toggle
    showing/hiding the typed password."""
    le = QLineEdit()
    le.setPlaceholderText(placeholder)
    le.setEchoMode(QLineEdit.EchoMode.Password)
    le.setFixedHeight(height)

    action = le.addAction(eye_icon(slashed=False), QLineEdit.ActionPosition.TrailingPosition)

    def _toggle():
        if le.echoMode() == QLineEdit.EchoMode.Password:
            le.setEchoMode(QLineEdit.EchoMode.Normal)
            action.setIcon(eye_icon(slashed=True))
        else:
            le.setEchoMode(QLineEdit.EchoMode.Password)
            action.setIcon(eye_icon(slashed=False))

    action.triggered.connect(_toggle)
    return le
