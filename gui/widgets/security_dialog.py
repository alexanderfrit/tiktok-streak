"""WinUI 3 Fluent modal dialog explaining local encryption, network scope, and self-hosted privacy."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    HyperlinkButton,
    MessageBoxBase,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from gui.services.i18n import tr
from gui.services.storage import default_config_dir
from gui.services.theme import escape_qt_mnemonic

SOURCE_REPO_URL = "https://github.com/alexanderfrit/tiktok-streak"


class SecurityPrivacyDialog(MessageBoxBase):
    """Modal dialog detailing how cookies, tokens, and cloud secrets are encrypted and handled."""

    def __init__(self, parent: QWidget, config_dir: str | Path | None = None) -> None:
        super().__init__(parent)
        self._config_dir = Path(config_dir) if config_dir is not None else default_config_dir()

        self.titleLabel = SubtitleLabel(tr("security.dialog_title"), self)
        self.subtitleLabel = BodyLabel(tr("security.dialog_subtitle"), self)
        self.subtitleLabel.setWordWrap(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)

        points = [
            ("security.point1_title", "security.point1_desc"),
            ("security.point2_title", "security.point2_desc"),
            ("security.point3_title", "security.point3_desc"),
            ("security.point4_title", "security.point4_desc"),
        ]
        self.pointCards: list[tuple[SimpleCardWidget, StrongBodyLabel, CaptionLabel]] = []
        for title_key, desc_key in points:
            card = SimpleCardWidget(self)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(4)
            t_lbl = StrongBodyLabel(tr(title_key), card)
            t_lbl.setWordWrap(True)
            d_lbl = CaptionLabel(tr(desc_key), card)
            d_lbl.setWordWrap(True)
            lay.addWidget(t_lbl)
            lay.addWidget(d_lbl)
            self.viewLayout.addWidget(card)
            self.pointCards.append((card, t_lbl, d_lbl))

        links_row = QHBoxLayout()
        links_row.setContentsMargins(0, 4, 0, 0)
        links_row.setSpacing(10)
        self.openConfigBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("security.open_config_btn")), self
        )
        self.openConfigBtn.clicked.connect(self._open_config_folder)
        self.openRepoBtn = HyperlinkButton(
            SOURCE_REPO_URL, escape_qt_mnemonic(tr("security.open_repo_btn")), self, FIF.GITHUB
        )
        links_row.addWidget(self.openConfigBtn)
        links_row.addWidget(self.openRepoBtn)
        links_row.addStretch(1)
        self.viewLayout.addLayout(links_row)

        self.yesButton.setText(tr("common.close"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(580)

    def _open_config_folder(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_dir)))


__all__ = ["SOURCE_REPO_URL", "SecurityPrivacyDialog"]
