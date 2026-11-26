"""WinUI 3 Fluent modal dialog explaining local encryption, network scope, and self-hosted privacy."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
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
    TransparentPushButton,
    isDarkTheme,
)

from gui.services.i18n import tr
from gui.services.storage import default_config_dir
from gui.services.theme import INNER_RADIUS, escape_qt_mnemonic
from gui.widgets.fluent_redesign import apply_fluent_dialog_footer

SOURCE_REPO_URL = "https://github.com/alexanderfrit/tiktok-streak"


class SecurityPrivacyDialog(MessageBoxBase):
    """Modal dialog with plain-language privacy summary and collapsible technical audit details."""

    def __init__(self, parent: QWidget, config_dir: str | Path | None = None) -> None:
        super().__init__(parent)
        self._config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
        self._tech_expanded = False

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
            lay.setContentsMargins(12, 9, 12, 9)
            lay.setSpacing(2)
            t_lbl = StrongBodyLabel(tr(title_key), card)
            t_lbl.setWordWrap(True)
            d_lbl = CaptionLabel(tr(desc_key), card)
            d_lbl.setWordWrap(True)
            lay.addWidget(t_lbl)
            lay.addWidget(d_lbl)
            self.viewLayout.addWidget(card)
            self.pointCards.append((card, t_lbl, d_lbl))

        # Progressive disclosure: Technical & audit specs (collapsed by default)
        tech_toggle_row = QHBoxLayout()
        tech_toggle_row.setContentsMargins(0, 2, 0, 0)
        tech_toggle_row.setSpacing(6)
        self.techToggleBtn = TransparentPushButton(
            FIF.CHEVRON_RIGHT,
            escape_qt_mnemonic(tr("security.tech_details_toggle")),
            self,
        )
        self.techToggleBtn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.techToggleBtn.clicked.connect(self._toggle_tech_details)
        tech_toggle_row.addWidget(self.techToggleBtn)
        tech_toggle_row.addStretch(1)
        self.viewLayout.addLayout(tech_toggle_row)

        self.techDetailsBox = QFrame(self)
        self.techDetailsBox.setObjectName("SecurityTechBox")
        dark = isDarkTheme()
        t_bg = "rgba(255, 255, 255, 0.04)" if dark else "#f5f7fa"
        t_border = "rgba(255, 255, 255, 0.09)" if dark else "#e2e6ec"
        self.techDetailsBox.setStyleSheet(
            f"QFrame#SecurityTechBox {{ background-color: {t_bg}; border: 1px solid {t_border}; "
            f"border-radius: {INNER_RADIUS}px; }}"
        )
        t_lay = QVBoxLayout(self.techDetailsBox)
        t_lay.setContentsMargins(12, 10, 12, 10)
        t_lay.setSpacing(4)
        self.techDetailsLabel = CaptionLabel(tr("security.tech_details_body"), self.techDetailsBox)
        self.techDetailsLabel.setWordWrap(True)
        self.techDetailsLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        t_lay.addWidget(self.techDetailsLabel)
        self.techDetailsBox.hide()
        self.viewLayout.addWidget(self.techDetailsBox)

        # Fluent footer: Open Folder & View Source on the left, compact Close right-aligned
        self.openConfigBtn = PushButton(
            FIF.FOLDER, escape_qt_mnemonic(tr("security.open_config_btn")), self
        )
        self.openConfigBtn.clicked.connect(self._open_config_folder)
        self.openRepoBtn = HyperlinkButton(
            SOURCE_REPO_URL, escape_qt_mnemonic(tr("security.open_repo_btn")), self, FIF.GITHUB
        )

        self.yesButton.setText(escape_qt_mnemonic(tr("common.close")))
        self.cancelButton.hide()
        apply_fluent_dialog_footer(
            self,
            left_widgets=[self.openConfigBtn, self.openRepoBtn],
            right_widgets=[self.yesButton],
        )
        self.widget.setMinimumWidth(520)

    def _toggle_tech_details(self) -> None:
        self._tech_expanded = not self._tech_expanded
        self.techDetailsBox.setVisible(self._tech_expanded)
        self.techToggleBtn.setIcon(
            FIF.CHEVRON_DOWN_MED if self._tech_expanded else FIF.CHEVRON_RIGHT
        )

    def _open_config_folder(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_dir)))


__all__ = ["SOURCE_REPO_URL", "SecurityPrivacyDialog"]
