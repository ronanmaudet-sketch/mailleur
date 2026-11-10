"""Interface Python/Qt à trois pages ; les calculs sont délégués au noyau Rust."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTabBar, QTableWidget,
    QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .projects import default_examples, load_portable_project, save_portable_project, strict_json
from .viewport import MeshViewport


UNITS = {"mm": 1e-3, "cm": 1e-2, "m": 1.0, "in": .0254}


class CalculationThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try: self.succeeded.emit(self.function())
        except Exception as error: self.failed.emit(str(error))


def _title(text, *, eyebrow=None):
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 8)
    layout.setSpacing(5)
    if eyebrow:
        label = QLabel(eyebrow.upper())
        label.setObjectName("eyebrow")
        layout.addWidget(label)
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    label.setWordWrap(True)
    layout.addWidget(label)
    return widget


class AtelierWindow(QMainWindow):
    """Fenêtre principale, instanciable sans lancer de calcul ni ouvrir de fichier."""

    def __init__(self, workspace=None, session=None):
        super().__init__()
        self.workspace = Path(workspace or Path.home() / "AtelierMailleur").expanduser()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.source = None
        self.project = copy.deepcopy(default_examples()[0]["project"])
        self.project.setdefault("units", {"input": "mm", "internal": "m"})
        self.result = None
        self.result_project = None
        self._pending_project = None
        self._pending_operation = None
        self._pending_corpus_operation = None
        self._result_operation = None
        self.calculation_thread = None
        self._loading = False
        self._closing = False
        self._example_items = default_examples()
        self.setWindowTitle("Atelier Mailleur — Géométrie · Maillage · Exportation")
        self.resize(1500, 940)
        self.setMinimumSize(1080, 720)
        self._build()
        style = Path(__file__).with_name("desktop.qss")
        if style.is_file(): self.setStyleSheet(style.read_text(encoding="utf-8"))
        self.load_project(self.project)

    def _build(self):
        central = QWidget()
        main = QVBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.setCentralWidget(central)
        header = QFrame()
        header.setObjectName("appHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 13, 22, 13)
        brand = QLabel("◈  ATELIER <b>MAILLEUR</b>")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(30)
        self.name = QLineEdit("Plaque percée")
        self.name.setObjectName("projectName")
        self.name.setMaximumWidth(360)
        layout.addWidget(self.name)
        layout.addStretch()
        badge = QLabel("  PRÉPARATION AU CALCUL  ")
        badge.setObjectName("architectureBadge")
        layout.addWidget(badge)
        main.addWidget(header)
        workflow = QFrame()
        workflow.setObjectName("workflowBar")
        row = QHBoxLayout(workflow)
        row.setContentsMargins(20, 0, 20, 0)
        self.pages = QTabBar()
        self.pages.setObjectName("workflowTabs")
        self.pages.setExpanding(False)
        for text in ("01   Géométrie", "02   Maillage", "03   Exportation"):
            self.pages.addTab(text)
        row.addWidget(self.pages)
        row.addStretch()
        for text, callback in (("Ouvrir", self.open_project), ("Enregistrer", self.save_project)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            row.addWidget(button)
        main.addWidget(workflow)
        self.ribbon = QStackedWidget()
        self.ribbon.setObjectName("ribbon")
        self.ribbon.setFixedHeight(81)
        self.ribbon.addWidget(self._ribbon_geometry())
        self.ribbon.addWidget(self._ribbon_mesh())
        self.ribbon.addWidget(self._ribbon_export())
        main.addWidget(self.ribbon)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        navigator = QFrame()
        navigator.setObjectName("navigator")
        nav = QVBoxLayout(navigator)
        nav.setContentsMargins(15, 17, 15, 14)
        nav.addWidget(_title("Navigateur du modèle", eyebrow="Pièce & sélection"))
        self.model_tree = QTreeWidget()
        self.model_tree.setObjectName("modelTree")
        self.model_tree.setHeaderHidden(True)
        self.model_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.model_tree.itemSelectionChanged.connect(self.tree_selection_changed)
        nav.addWidget(self.model_tree, 1)
        self.selection_label = QLabel("Aucune entité sélectionnée")
        self.selection_label.setObjectName("selectionLabel")
        self.selection_label.setWordWrap(True)
        nav.addWidget(self.selection_label)
        clear = QPushButton("Effacer la sélection")
        clear.clicked.connect(self.model_tree.clearSelection)
        nav.addWidget(clear)
        splitter.addWidget(navigator)
        view_container = QWidget()
        view_layout = QVBoxLayout(view_container)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(0)
        view_bar = QFrame()
        view_bar.setObjectName("viewBar")
        buttons = QHBoxLayout(view_bar)
        buttons.setContentsMargins(17, 9, 15, 9)
        self.view_label = QLabel("Vue du modèle")
        buttons.addWidget(self.view_label)
        buttons.addStretch()
        self.viewport = MeshViewport()
        for text, fn in (("Cadrer", self.viewport.fit), ("ISO", self.viewport.view_iso), ("XY", self.viewport.view_xy)):
            button = QPushButton(text)
            button.setObjectName("viewButton")
            button.clicked.connect(fn)
            buttons.addWidget(button)
        self.edges = QCheckBox("Arêtes")
        self.edges.setChecked(True)
        self.edges.toggled.connect(self.toggle_edges)
        buttons.addWidget(self.edges)
        view_layout.addWidget(view_bar)
        view_layout.addWidget(self.viewport, 1)
        self.viewport.entitySelected.connect(self.viewport_selected)
        self.viewport.statusMessage.connect(self.set_status)
        splitter.addWidget(view_container)
        self.properties = QStackedWidget()
        self.properties.setObjectName("propertyPages")
        for widget in (self._geometry_page(), self._mesh_page(), self._export_page()):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(widget)
            self.properties.addWidget(scroll)
        splitter.addWidget(self.properties)
        splitter.setSizes([245, 825, 430])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        navigator.setMinimumWidth(200)
        self.properties.setMinimumWidth(360)
        self.properties.setMaximumWidth(470)
        main.addWidget(splitter, 1)
        self.log = QPlainTextEdit()
        self.log.setObjectName("calculationLog")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(125)
        self.log.hide()
        main.addWidget(self.log)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setMaximumHeight(12)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        journal = QPushButton("Journal")
        journal.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible()))
        self.statusBar().addPermanentWidget(journal)
        self.pages.currentChanged.connect(self.change_page)
        self.change_page(0)

    def _ribbon_button(self, text, callback, *, primary=False):
        button = QPushButton(text)
        button.setMinimumHeight(42)
        if primary: button.setObjectName("primaryButton")
        button.clicked.connect(callback)
        return button

    def _ribbon_row(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(12)
        return widget, layout

    def _ribbon_geometry(self):
        widget, row = self._ribbon_row()
        row.addWidget(self._ribbon_button("＋  Importer une pièce", self.import_geometry, primary=True))
        row.addWidget(self._ribbon_button("Examiner la géométrie", self.inspect_geometry))
        row.addSpacing(13)
        row.addWidget(QLabel("Exemple"))
        self.examples = QComboBox()
        self.examples.setMinimumWidth(220)
        for item in self._example_items: self.examples.addItem(item["name"], item["id"])
        row.addWidget(self.examples)
        row.addWidget(self._ribbon_button("Charger", self.load_example))
        row.addStretch()
        return widget

    def _ribbon_mesh(self):
        widget, row = self._ribbon_row()
        self.generate_button = self._ribbon_button("▶  Générer le maillage", self.generate_mesh, primary=True)
        row.addWidget(self.generate_button)
        self.remesh_button = self._ribbon_button("Remailler la sélection", self.remesh_selected)
        row.addWidget(self.remesh_button)
        self.learn_ribbon_button = self._ribbon_button("Apprendre cette pièce", self.learn_current_piece)
        row.addWidget(self.learn_ribbon_button)
        self.cancel_button = self._ribbon_button("Arrêter le calcul", self.cancel_calculation)
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        row.addStretch()
        label = QLabel("Qualité et zones protégées contrôlées")
        label.setWordWrap(True)
        row.addWidget(label)
        return widget

    def _ribbon_export(self):
        widget, row = self._ribbon_row()
        self.export_button = self._ribbon_button("↓  Exporter le maillage", self.export_mesh, primary=True)
        row.addWidget(self.export_button)
        row.addWidget(self._ribbon_button("Enregistrer le projet", self.save_project))
        row.addStretch()
        row.addWidget(QLabel("Géométrie · unités · groupes · connectivité"))
        return widget

    def _page(self, title, subtitle):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(22, 23, 22, 23)
        layout.setSpacing(14)
        layout.addWidget(_title(title, eyebrow=subtitle))
        return widget, layout

    def _double(self, value=0, minimum=0, maximum=1e12, decimals=6):
        field = QDoubleSpinBox()
        field.setDecimals(decimals)
        field.setRange(minimum, maximum)
        field.setValue(value)
        field.setSingleStep(.1)
        field.setKeyboardTracking(False)
        return field

    def _units_combo(self, name):
        combo = QComboBox()
        combo.setObjectName(name)
        for value, label in (("mm", "Millimètres · mm"), ("cm", "Centimètres · cm"), ("m", "Mètres · m"), ("in", "Pouces · in")):
            combo.addItem(label, value)
        return combo

    def _geometry_page(self):
        widget, layout = self._page("Préparer la pièce", "01 / Géométrie")
        self.source_label = QLabel("Géométrie paramétrique")
        self.source_label.setObjectName("sourceLabel")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        form = QFormLayout()
        form.setSpacing(12)
        self.mode = QComboBox()
        for label, value in (("2D plane", "planar"), ("Coque / surface 3D", "surface"), ("Volume 3D", "volume")):
            self.mode.addItem(label, value)
        self.mode.setObjectName("environmentMode")
        form.addRow("Environnement", self.mode)
        self.input_units = self._units_combo("inputUnits")
        form.addRow("Unités de la pièce", self.input_units)
        self.kind = QComboBox()
        for label, value in (("Plaque percée", "plate"), ("Bloc", "box"), ("Sphère", "sphere"), ("Équerre 3D", "bracket"), ("Contour polygonal", "polygon"), ("Pièce importée", "import")):
            self.kind.addItem(label, value)
        form.addRow("Géométrie", self.kind)
        layout.addLayout(form)
        self.dimensions_group = QGroupBox("Dimensions du modèle")
        dim_form = QFormLayout(self.dimensions_group)
        self.geometry_fields = {}
        self.geometry_rows = {}
        for key, label, value in (("width", "Largeur", 10), ("height", "Hauteur", 6), ("depth", "Profondeur", 3),
                                  ("thickness", "Épaisseur", 2), ("radius", "Rayon", 3), ("wall", "Paroi", 1),
                                  ("throughhole_radius", "Rayon du perçage", .35), ("corner_radius", "Congé extérieur", .5),
                                  ("chamfer", "Chanfrein", 0)):
            field = self._double(value)
            field.setObjectName("geometry_" + key)
            self.geometry_fields[key] = field
            dim_form.addRow(label, field)
            self.geometry_rows[key] = (dim_form.labelForField(field), field)
        layout.addWidget(self.dimensions_group)
        self.holes_group = QGroupBox("Perçages circulaires")
        holes_layout = QVBoxLayout(self.holes_group)
        self.holes = QTableWidget(0, 3)
        self.holes.setHorizontalHeaderLabels(["Centre X", "Centre Y", "Rayon"])
        self.holes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.holes.setMaximumHeight(155)
        holes_layout.addWidget(self.holes)
        hole_actions = QHBoxLayout()
        add = QPushButton("＋ Trou")
        add.clicked.connect(lambda: self.add_hole())
        remove = QPushButton("Retirer")
        remove.clicked.connect(lambda: self.holes.removeRow(self.holes.currentRow()) if self.holes.currentRow() >= 0 else None)
        hole_actions.addWidget(add)
        hole_actions.addWidget(remove)
        holes_layout.addLayout(hole_actions)
        layout.addWidget(self.holes_group)
        self.polygon_group = QGroupBox("Contours polygonaux")
        polygon_layout = QVBoxLayout(self.polygon_group)
        polygon_layout.addWidget(QLabel("Contour extérieur [x, y]"))
        self.polygon_outer = QPlainTextEdit()
        self.polygon_outer.setMaximumHeight(90)
        polygon_layout.addWidget(self.polygon_outer)
        polygon_layout.addWidget(QLabel("Trous · liste de contours"))
        self.polygon_holes = QPlainTextEdit("[]")
        self.polygon_holes.setMaximumHeight(80)
        polygon_layout.addWidget(self.polygon_holes)
        layout.addWidget(self.polygon_group)
        transform = QGroupBox("Petites modifications")
        t_form = QFormLayout(transform)
        self.translation = []
        for axis in "XYZ":
            field = self._double(0, -1e12)
            field.setObjectName("translation" + axis)
            self.translation.append(field)
            t_form.addRow("Déplacer " + axis, field)
        self.scale = self._double(1, 1e-9)
        t_form.addRow("Facteur d'échelle", self.scale)
        layout.addWidget(transform)
        repair = QGroupBox("Préparation des défauts")
        r_form = QFormLayout(repair)
        self.repair_enabled = QCheckBox("Activer la réparation contrôlée")
        r_form.addRow(self.repair_enabled)
        self.repair_tolerance = self._double(1e-7, 1e-12, decimals=12)
        r_form.addRow("Tolérance", self.repair_tolerance)
        self.make_solids = QCheckBox("Reconstruire les enveloppes fermées")
        r_form.addRow(self.make_solids)
        layout.addWidget(repair)
        hint = QLabel("Les trous voulus sont conservés. La réparation ne devine pas quelles ouvertures devraient être bouchées.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.kind.currentIndexChanged.connect(self.update_geometry_visibility)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        return widget

    def _mesh_page(self):
        widget, layout = self._page("Définir le maillage", "02 / Conditions & génération")
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem("Toute la pièce", "all")
        self.scope.addItem("Les entités sélectionnées", "entities")
        self.scope.setObjectName("meshScope")
        form.addRow("Zone à mailler", self.scope)
        self.mesh_size = self._double(.65, 1e-12)
        self.mesh_size.setObjectName("meshSize")
        self.min_size = self._double(.18, 1e-12)
        form.addRow("Taille globale", self.mesh_size)
        form.addRow("Taille minimale", self.min_size)
        self.order = QComboBox()
        self.order.addItem("Linéaire · ordre 1", 1)
        self.order.addItem("Quadratique · ordre 2", 2)
        form.addRow("Ordre", self.order)
        self.algorithm = QComboBox()
        for label, value in (("Automatique", "automatic"), ("Delaunay", "delaunay"), ("Frontal", "frontal")):
            self.algorithm.addItem(label, value)
        form.addRow("Algorithme", self.algorithm)
        self.recombine = QCheckBox("Recombiner en quadrilatères")
        form.addRow(self.recombine)
        self.calculation_threads = QSpinBox()
        self.calculation_threads.setRange(1, 32)
        self.calculation_threads.setValue(1)
        form.addRow("Fils de calcul", self.calculation_threads)
        self.max_elements = QSpinBox()
        self.max_elements.setRange(10, 2000000)
        self.max_elements.setValue(200000)
        self.max_elements.setSingleStep(10000)
        form.addRow("Limite d'éléments", self.max_elements)
        layout.addLayout(form)
        local = QGroupBox("Réglages locaux sur la sélection")
        local_layout = QVBoxLayout(local)
        self.local_size = self._double(.25, 1e-12)
        local_layout.addWidget(QLabel("Taille locale, dans les unités de la pièce"))
        local_layout.addWidget(self.local_size)
        local_button = QPushButton("Affecter aux entités sélectionnées")
        local_button.clicked.connect(self.add_local_sizes)
        local_layout.addWidget(local_button)
        self.local_table = QTableWidget(0, 3)
        self.local_table.setHorizontalHeaderLabels(["Dim.", "Entité", "Taille"])
        self.local_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.local_table.setMaximumHeight(150)
        local_layout.addWidget(self.local_table)
        clear_local = QPushButton("Effacer les tailles locales")
        clear_local.clicked.connect(lambda: self.local_table.setRowCount(0))
        local_layout.addWidget(clear_local)
        layout.addWidget(local)
        quality = QGroupBox("Critères d'acceptation")
        quality_form = QFormLayout(quality)
        self.minimum_quality = self._double(.05, 0, 1, decimals=4)
        self.minimum_quality.setObjectName("minimumQuality")
        quality_form.addRow("Qualité minimale partout", self.minimum_quality)
        self.poor_threshold = self._double(.2, 1e-6, 1, decimals=4)
        self.poor_threshold.setObjectName("poorThreshold")
        quality_form.addRow("Seuil des éléments médiocres", self.poor_threshold)
        self.limit_poor_count = QCheckBox("Limiter leur nombre")
        self.max_poor_count = QSpinBox()
        self.max_poor_count.setRange(0, 2000000)
        self.max_poor_count.setEnabled(False)
        self.limit_poor_count.toggled.connect(self.max_poor_count.setEnabled)
        quality_form.addRow(self.limit_poor_count, self.max_poor_count)
        self.limit_poor_fraction = QCheckBox("Limiter leur proportion (%)")
        self.max_poor_fraction = self._double(1, 0, 100, decimals=3)
        self.max_poor_fraction.setEnabled(False)
        self.limit_poor_fraction.toggled.connect(self.max_poor_fraction.setEnabled)
        quality_form.addRow(self.limit_poor_fraction, self.max_poor_fraction)
        self.strict_quality = QCheckBox("Refuser un maillage hors critères")
        self.strict_quality.setObjectName("strictQuality")
        self.strict_quality.setChecked(True)
        quality_form.addRow(self.strict_quality)
        layout.addWidget(quality)
        zones = QGroupBox("Zones critiques à protéger")
        zones_layout = QVBoxLayout(zones)
        zone_form = QFormLayout()
        self.zone_name = QLineEdit()
        self.zone_name.setPlaceholderText("Nom de la zone")
        zone_form.addRow("Nom", self.zone_name)
        self.zone_kind = QComboBox()
        for label, value in (("Contact", "contact"), ("Raccord de maillage", "interface"),
                             ("Fortes contraintes désignées", "stress"), ("Autre zone sensible", "custom")):
            self.zone_kind.addItem(label, value)
        self.zone_kind.setObjectName("criticalZoneKind")
        zone_form.addRow("Rôle", self.zone_kind)
        self.zone_minimum = self._double(.35, 0, 1, decimals=4)
        zone_form.addRow("Qualité minimale locale", self.zone_minimum)
        self.zone_buffer = self._double(0, 0, decimals=6)
        zone_form.addRow("Marge autour de la zone", self.zone_buffer)
        zones_layout.addLayout(zone_form)
        add_zone = QPushButton("Protéger les entités sélectionnées")
        add_zone.setObjectName("addCriticalZone")
        add_zone.clicked.connect(self.add_critical_zone)
        zones_layout.addWidget(add_zone)
        self.zone_table = QTableWidget(0, 4)
        self.zone_table.setObjectName("criticalZones")
        self.zone_table.setHorizontalHeaderLabels(["Zone", "Entités", "Qualité", "Marge"])
        self.zone_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.zone_table.setMaximumHeight(180)
        self.zone_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.zone_table.setSelectionMode(QTableWidget.SingleSelection)
        self.zone_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.zone_table.itemSelectionChanged.connect(self.select_critical_zone)
        zones_layout.addWidget(self.zone_table)
        zone_actions = QHBoxLayout()
        remove_zone = QPushButton("Retirer la zone")
        remove_zone.clicked.connect(self.remove_critical_zone)
        zone_actions.addWidget(remove_zone)
        clear_zones = QPushButton("Tout effacer")
        clear_zones.clicked.connect(lambda: self.zone_table.setRowCount(0))
        zone_actions.addWidget(clear_zones)
        zones_layout.addLayout(zone_actions)
        zone_hint = QLabel("Sélectionnez des courbes, faces ou volumes après examen de la pièce. La marge utilise les unités de la pièce. Les régions de fortes contraintes sont désignées ici ; la géométrie seule ne permet pas de les déduire.")
        zone_hint.setObjectName("hint")
        zone_hint.setWordWrap(True)
        zones_layout.addWidget(zone_hint)
        layout.addWidget(zones)
        continuous = QGroupBox("Apprendre sur cette pièce")
        continuous_layout = QVBoxLayout(continuous)
        search_form = QFormLayout()
        self.learning_trials = QSpinBox()
        self.learning_trials.setRange(1, 48)
        self.learning_trials.setValue(12)
        self.learning_trials.setObjectName("learningTrials")
        search_form.addRow("Nombre maximal d'essais", self.learning_trials)
        self.learning_epochs = QSpinBox()
        self.learning_epochs.setRange(1, 200)
        self.learning_epochs.setValue(100)
        search_form.addRow("Passages d'apprentissage", self.learning_epochs)
        self.use_memory = QCheckBox("Réutiliser les expériences enregistrées")
        self.use_memory.setChecked(True)
        self.use_memory.setObjectName("useExperienceMemory")
        search_form.addRow(self.use_memory)
        continuous_layout.addLayout(search_form)
        self.learn_button = QPushButton("Explorer, apprendre et retenir cette pièce")
        self.learn_button.setObjectName("learnCurrentPiece")
        self.learn_button.clicked.connect(self.learn_current_piece)
        continuous_layout.addWidget(self.learn_button)
        self.experience_summary = QLabel("La recherche porte sur la pièce ouverte, ses tailles locales et ses zones protégées. Elle est bornée au nombre d'essais indiqué ; un résultat mémorisé est contrôlé à nouveau.")
        self.experience_summary.setObjectName("experienceSummary")
        self.experience_summary.setWordWrap(True)
        continuous_layout.addWidget(self.experience_summary)
        self.experience_history = QTableWidget(0, 4)
        self.experience_history.setObjectName("experienceTrials")
        self.experience_history.setHorizontalHeaderLabels(["Essai", "Stratégie", "Qualité", "État"])
        self.experience_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.experience_history.setMaximumHeight(175)
        self.experience_history.setEditTriggers(QTableWidget.NoEditTriggers)
        continuous_layout.addWidget(self.experience_history)
        self.memory_button = QPushButton("Voir la mémoire d'apprentissage")
        self.memory_button.clicked.connect(self.show_memory)
        continuous_layout.addWidget(self.memory_button)
        layout.addWidget(continuous)
        learning = QGroupBox("Guide du corpus de démonstration")
        learning_layout = QVBoxLayout(learning)
        self.use_guide = QCheckBox("Consulter le guide pour choisir l'algorithme")
        self.use_guide.setObjectName("useLearnedGuide")
        self.use_guide.setToolTip("Cette option s'applique au prochain calcul. Le rapport conserve le choix réellement utilisé pour le maillage actuel.")
        learning_layout.addWidget(self.use_guide)
        self.train_button = QPushButton("Entraîner sur les formes de démonstration")
        self.train_button.setObjectName("trainLearnedGuide")
        self.train_button.clicked.connect(self.train_guide)
        learning_layout.addWidget(self.train_button)
        self.learning_summary = QLabel("Ce guide distinct utilise le petit corpus de démonstration, pas la pièce ouverte. Son utilisation reste soumise à une validation sur des formes réservées.")
        self.learning_summary.setObjectName("learningSummary")
        self.learning_summary.setWordWrap(True)
        learning_layout.addWidget(self.learning_summary)
        layout.addWidget(learning)
        layout.addWidget(self._corpus_panel())
        correspondence = QGroupBox("Correspondances de maillage")
        c_form = QFormLayout(correspondence)
        self.master = QSpinBox()
        self.slave = QSpinBox()
        for field in (self.master, self.slave): field.setRange(1, 100000000)
        c_form.addRow("Entité maître", self.master)
        c_form.addRow("Entité esclave", self.slave)
        self.periodic_dimension = QComboBox()
        self.periodic_dimension.addItem("Courbes · dimension 1", 1)
        self.periodic_dimension.addItem("Faces · dimension 2", 2)
        c_form.addRow("Correspondance", self.periodic_dimension)
        self.periodic_translation = []
        for axis in "XYZ":
            field = self._double(0, -1e12)
            self.periodic_translation.append(field)
            c_form.addRow("Translation " + axis, field)
        add = QPushButton("Ajouter la correspondance")
        add.clicked.connect(self.add_correspondence)
        c_form.addRow(add)
        self.correspondence_table = QTableWidget(0, 4)
        self.correspondence_table.setHorizontalHeaderLabels(["Dim.", "Maître", "Esclave", "Translation"])
        self.correspondence_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.correspondence_table.setMaximumHeight(135)
        c_form.addRow(self.correspondence_table)
        clear = QPushButton("Effacer les correspondances")
        clear.clicked.connect(lambda: self.correspondence_table.setRowCount(0))
        c_form.addRow(clear)
        layout.addWidget(correspondence)
        self.quality_label = QLabel("Générez un maillage pour examiner sa qualité.")
        self.quality_label.setObjectName("qualitySummary")
        self.quality_label.setWordWrap(True)
        layout.addWidget(self.quality_label)
        self.zone_quality_table = QTableWidget(0, 4)
        self.zone_quality_table.setObjectName("criticalZoneQuality")
        self.zone_quality_table.setHorizontalHeaderLabels(["Zone", "Éléments", "Minimum", "Hors seuil"])
        self.zone_quality_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.zone_quality_table.setMaximumHeight(165)
        self.zone_quality_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.zone_quality_table)
        hint = QLabel("Le remaillage local porte sur des faces ou volumes identifiés. Le noyau vérifie les entités conservées ; l'édition libre d'éléments individuels n'est pas proposée dans cette version.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        return widget

    def _corpus_panel(self):
        """Controls only: the Rust process plans, generates, measures and trains."""
        panel = QGroupBox()
        panel.setObjectName("corpusPanel")
        layout = QVBoxLayout(panel)
        toggle = QToolButton()
        toggle.setText("Corpus et entraînement par lots")
        toggle.setObjectName("corpusToggle")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.RightArrow)
        layout.addWidget(toggle)
        self.corpus_body = QWidget()
        self.corpus_body.setObjectName("corpusBody")
        body = QVBoxLayout(self.corpus_body)
        body.setContentsMargins(0, 7, 0, 0)
        explanation = QLabel("Le plan fixe un objectif. Il ne lance aucun maillage. Les lots sont calculés, contrôlés et enregistrés par Rust ; un lot interrompu peut être repris.")
        explanation.setWordWrap(True)
        explanation.setObjectName("hint")
        body.addWidget(explanation)
        path_row = QHBoxLayout()
        self.corpus_directory = QLineEdit(str(self.workspace / "corpus"))
        self.corpus_directory.setObjectName("corpusDirectory")
        path_row.addWidget(self.corpus_directory, 1)
        choose = QPushButton("Choisir…")
        choose.setObjectName("chooseCorpusDirectory")
        choose.clicked.connect(self.choose_corpus_directory)
        path_row.addWidget(choose)
        body.addWidget(QLabel("Dossier du corpus"))
        body.addLayout(path_row)
        plan = QFormLayout()
        self.corpus_structured = QSpinBox()
        self.corpus_structured.setObjectName("corpusStructured")
        self.corpus_structured.setRange(0, 1000000000)
        self.corpus_structured.setValue(250000)
        plan.addRow("Configurations structurées", self.corpus_structured)
        self.corpus_random = QSpinBox()
        self.corpus_random.setObjectName("corpusRandom")
        self.corpus_random.setRange(0, 1000000000)
        self.corpus_random.setValue(750000)
        plan.addRow("Configurations aléatoires", self.corpus_random)
        self.corpus_seed = QSpinBox()
        self.corpus_seed.setObjectName("corpusSeed")
        self.corpus_seed.setRange(0, 2147483647)
        self.corpus_seed.setValue(42)
        plan.addRow("Graine reproductible", self.corpus_seed)
        self.corpus_strategies = QSpinBox()
        self.corpus_strategies.setRange(1, 4)
        self.corpus_strategies.setValue(4)
        plan.addRow("Stratégies par configuration", self.corpus_strategies)
        body.addLayout(plan)
        self.corpus_plan_button = QPushButton("Créer le plan · sans lancer les calculs")
        self.corpus_plan_button.setObjectName("planCorpus")
        self.corpus_plan_button.clicked.connect(self.plan_corpus)
        body.addWidget(self.corpus_plan_button)
        batch = QFormLayout()
        self.corpus_batch_cases = QSpinBox()
        self.corpus_batch_cases.setObjectName("corpusBatchCases")
        self.corpus_batch_cases.setRange(1, 10000)
        self.corpus_batch_cases.setValue(20)
        batch.addRow("Configurations dans ce lot", self.corpus_batch_cases)
        self.corpus_seconds = QSpinBox()
        self.corpus_seconds.setObjectName("corpusSeconds")
        self.corpus_seconds.setRange(1, 86400)
        self.corpus_seconds.setValue(240)
        self.corpus_seconds.setToolTip("Budget vérifié entre les cas ou mini-lots. Un calcul en cours reste soumis au délai global de la session.")
        batch.addRow("Budget de ce traitement (s)", self.corpus_seconds)
        self.corpus_keep_meshes = QCheckBox("Conserver aussi les fichiers des maillages")
        self.corpus_keep_meshes.setChecked(False)
        self.corpus_keep_meshes.setToolTip("Les résultats et scores sont toujours conservés. Cette option conserve également les fichiers de calcul et augmente l'espace utilisé.")
        batch.addRow(self.corpus_keep_meshes)
        body.addLayout(batch)
        self.corpus_run_button = QPushButton("Calculer le prochain lot / reprendre")
        self.corpus_run_button.setObjectName("runCorpusBatch")
        self.corpus_run_button.clicked.connect(self.run_corpus_batch)
        body.addWidget(self.corpus_run_button)
        self.corpus_status_button = QPushButton("Actualiser l'état du corpus")
        self.corpus_status_button.setObjectName("corpusStatus")
        self.corpus_status_button.clicked.connect(self.show_corpus_status)
        body.addWidget(self.corpus_status_button)
        training = QFormLayout()
        self.corpus_epochs = QSpinBox()
        self.corpus_epochs.setRange(1, 200)
        self.corpus_epochs.setValue(3)
        training.addRow("Passages sur les données", self.corpus_epochs)
        self.corpus_training_batch = QSpinBox()
        self.corpus_training_batch.setRange(1, 4096)
        self.corpus_training_batch.setValue(128)
        training.addRow("Exemples par mini-lot", self.corpus_training_batch)
        self.corpus_training_records = QSpinBox()
        self.corpus_training_records.setRange(1, 1000000000)
        self.corpus_training_records.setValue(10000)
        training.addRow("Cas lus au maximum", self.corpus_training_records)
        body.addLayout(training)
        training_budget_hint = QLabel("Le budget d'entraînement concerne les mini-lots ; préparation et contrôles peuvent prolonger le traitement. Arrêt disponible.")
        training_budget_hint.setObjectName("corpusBudgetHint")
        training_budget_hint.setWordWrap(True)
        body.addWidget(training_budget_hint)
        self.corpus_train_button = QPushButton("Entraîner le réseau par mini-lots")
        self.corpus_train_button.setObjectName("trainCorpusBatches")
        self.corpus_train_button.clicked.connect(self.train_corpus_batches)
        body.addWidget(self.corpus_train_button)
        self.corpus_summary = QLabel("Aucun corpus consulté. Les nombres ci-dessus sont un objectif, pas des configurations déjà calculées.")
        self.corpus_summary.setObjectName("corpusSummary")
        self.corpus_summary.setWordWrap(True)
        body.addWidget(self.corpus_summary)
        self.corpus_partitions = QTableWidget(0, 2)
        self.corpus_partitions.setObjectName("corpusPartitions")
        self.corpus_partitions.setHorizontalHeaderLabels(["Jeu réservé", "Configurations"])
        self.corpus_partitions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.corpus_partitions.setMaximumHeight(130)
        self.corpus_partitions.setEditTriggers(QTableWidget.NoEditTriggers)
        body.addWidget(self.corpus_partitions)
        note = QLabel("Entraînement, validation et test final sont séparés par familles de géométries. Un classement appris n'est utilisé que si sa validation démontre un avantage. Le plan d'un million ne signifie pas que le million a été exécuté.")
        note.setWordWrap(True)
        note.setObjectName("hint")
        body.addWidget(note)
        layout.addWidget(self.corpus_body)
        self.corpus_body.hide()
        def expand(checked):
            self.corpus_body.setVisible(checked)
            toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        toggle.toggled.connect(expand)
        return panel

    def _export_page(self):
        widget, layout = self._page("Préparer l'échange", "03 / Exportation")
        form = QFormLayout()
        self.format = QComboBox()
        self.format.setObjectName("solverProfile")
        from .formats import FORMATS
        for value, info in FORMATS.items(): self.format.addItem(info["label"], value)
        form.addRow("Logiciel / format", self.format)
        self.export_units = self._units_combo("exportUnits")
        form.addRow("Unités exportées", self.export_units)
        layout.addLayout(form)
        self.export_notes = QLabel()
        self.export_notes.setObjectName("exportNotes")
        self.export_notes.setWordWrap(True)
        self.format.currentIndexChanged.connect(self.update_export_notes)
        layout.addWidget(self.export_notes)
        layout.addWidget(_title("Vérifications avant export"))
        self.export_summary = QLabel("Aucun maillage calculé.")
        self.export_summary.setWordWrap(True)
        layout.addWidget(self.export_summary)
        hint = QLabel("Les propriétés des matériaux, chargements et formulations se définissent dans le logiciel de calcul. Une relecture de format ne remplace pas une validation dans le solveur cible.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(320)
        layout.addWidget(self.diagnostics)
        layout.addStretch()
        self.update_export_notes()
        return widget

    def change_page(self, index):
        self.properties.setCurrentIndex(index)
        self.ribbon.setCurrentIndex(index)
        self.set_status(("Préparez la géométrie et vérifiez ses unités.", "Choisissez les conditions et la zone à mailler.", "Choisissez le solveur et les unités d'export.")[index])

    def update_geometry_visibility(self):
        kind, mode = self.kind.currentData(), self.mode.currentData()
        visible = {
            "plate": {"width", "height", "corner_radius", "chamfer", *( ["thickness"] if mode == "volume" else [])},
            "box": {"width", "height", "depth"}, "sphere": {"radius"},
            "bracket": {"width", "height", "depth", "wall", "throughhole_radius"},
        }.get(kind, set())
        for key, pair in self.geometry_rows.items():
            for widget in pair: widget.setVisible(key in visible)
        self.dimensions_group.setVisible(bool(visible))
        self.holes_group.setVisible(kind == "plate")
        self.polygon_group.setVisible(kind == "polygon")

    def mode_changed(self):
        volume = self.mode.currentData() == "volume"
        self.recombine.setEnabled(not volume)
        if volume: self.recombine.setChecked(False)
        if volume and self.repair_enabled.isChecked(): self.make_solids.setChecked(True)
        self.update_geometry_visibility()

    def update_export_notes(self):
        notes = {
            "med": "MED conserve les groupes de domaine et de frontière, y compris les groupes recouvrants.",
            "inp": "INP adapte les éléments au domaine plan, coque ou volume. Vérifier la formulation dans Abaqus ou CalculiX.",
            "msh": "MSH conserve les entités et groupes physiques du moteur natif.",
            "vtu": "VTU permet la visualisation et l'inspection des groupes dans ParaView.",
            "unv": "UNV transporte la connectivité et les groupes reconnus par le convertisseur.",
            "ansys": "APDL utilise MESH200 pour échanger le maillage. MESH200 n'a pas de degrés de liberté : affecter ensuite les éléments de calcul dans ANSYS.",
            "cast3m": "Le pont Cast3M produit un fichier UNV et un script d'import. Les deux fichiers doivent rester ensemble.",
        }
        self.export_notes.setText(notes.get(self.format.currentData(), ""))

    def set_status(self, message):
        self.statusBar().showMessage(message)

    def add_hole(self, hole=None):
        hole = hole or {"x": 5, "y": 3, "radius": .5}
        row = self.holes.rowCount()
        self.holes.insertRow(row)
        for column, key in enumerate(("x", "y", "radius")):
            self.holes.setItem(row, column, QTableWidgetItem(str(hole[key])))

    def load_project(self, project, source=None):
        self._loading = True
        self.project = copy.deepcopy(project)
        self.source = Path(source) if source else None
        self.result = None
        self.result_project = None
        self._result_operation = None
        self.name.setText(project.get("name", "Nouvelle pièce"))
        self.mode.setCurrentIndex(max(0, self.mode.findData(project.get("mode", "planar"))))
        self.kind.setCurrentIndex(max(0, self.kind.findData(project.get("geometry", {}).get("kind", "plate"))))
        self.input_units.setCurrentIndex(max(0, self.input_units.findData(project.get("units", {}).get("input", "mm"))))
        geometry, mesh, repair = project.get("geometry", {}), project.get("mesh", {}), project.get("repair", {})
        for key, field in self.geometry_fields.items(): field.setValue(float(geometry.get(key, 0 if key in ("chamfer", "corner_radius", "throughhole_radius") else 1)))
        self.holes.setRowCount(0)
        if geometry.get("kind") == "plate":
            for hole in geometry.get("holes", []): self.add_hole(hole)
        self.polygon_outer.setPlainText(json.dumps(geometry.get("outer", [[0, 0], [8, 0], [8, 5], [0, 5]])))
        self.polygon_holes.setPlainText(json.dumps(geometry.get("holes", []) if geometry.get("kind") == "polygon" else []))
        self.mesh_size.setValue(float(mesh.get("size", .5)))
        self.min_size.setValue(float(mesh.get("min_size", .1)))
        self.order.setCurrentIndex(max(0, self.order.findData(mesh.get("order", 1))))
        self.algorithm.setCurrentIndex(max(0, self.algorithm.findData(mesh.get("algorithm", "automatic"))))
        self.recombine.setChecked(bool(mesh.get("recombine", False)))
        self.calculation_threads.setValue(mesh.get("threads", 1))
        self.max_elements.setValue(mesh.get("max_elements", 200000))
        self.repair_enabled.setChecked(bool(repair.get("enabled", False)))
        self.make_solids.setChecked(bool(repair.get("make_solids", project.get("mode") == "volume")))
        self.repair_tolerance.setValue(float(repair.get("tolerance", 1e-7)))
        transform = geometry.get("transform", {})
        for field, value in zip(self.translation, transform.get("translation", [0, 0, 0])): field.setValue(float(value))
        self.scale.setValue(float(transform.get("scale", 1)))
        self.scope.setCurrentIndex(max(0, self.scope.findData(project.get("selection", {}).get("scope", "all"))))
        self.local_table.setRowCount(0)
        for item in project.get("local_sizes", []):
            self._append_local(item.get("dim", item.get("dimension")), item["tag"], item["size"], metadata=item)
        self.correspondence_table.setRowCount(0)
        for item in project.get("correspondences", []): self._append_correspondence(item)
        self.zone_table.setRowCount(0)
        for zone in project.get("critical_zones", []): self._append_critical_zone(zone)
        policy = project.get("quality_policy", {})
        self.minimum_quality.setValue(float(policy.get("minimum_quality", .05)))
        self.poor_threshold.setValue(float(policy.get("poor_threshold", .2)))
        self.strict_quality.setChecked(bool(policy.get("strict", True)))
        self.limit_poor_count.setChecked(policy.get("max_poor_elements") is not None)
        self.max_poor_count.setValue(int(policy.get("max_poor_elements") or 0))
        self.limit_poor_fraction.setChecked(policy.get("max_poor_fraction") is not None)
        self.max_poor_fraction.setValue(float(policy.get("max_poor_fraction", .01) or 0) * 100)
        learning = project.get("learning", {})
        self.learning_trials.setValue(int(learning.get("max_trials", 12)))
        self.learning_epochs.setValue(int(learning.get("epochs", 100)))
        self.use_memory.setChecked(bool(learning.get("use_memory", True)))
        self.experience_history.setRowCount(0)
        self.zone_quality_table.setRowCount(0)
        self.experience_summary.setText("La recherche porte sur la pièce ouverte et ses zones protégées. Les recettes mémorisées sont contrôlées à nouveau avant utilisation.")
        self.source_label.setText(self.source.name if self.source else "Géométrie paramétrique")
        self.model_tree.clear()
        top = QTreeWidgetItem([self.name.text()])
        top.setData(0, Qt.UserRole, None)
        self.model_tree.addTopLevelItem(top)
        self.viewport.clear()
        self.quality_label.setText("Aucun maillage calculé.")
        self.export_summary.setText("Aucun maillage calculé.")
        self.diagnostics.clear()
        self.mode_changed()
        self._loading = False

    def project_from_form(self):
        project = copy.deepcopy(self.project)
        project.update(schema_version=1, name=self.name.text().strip() or "Nouvelle pièce", mode=self.mode.currentData())
        kind = self.kind.currentData()
        geometry = {"kind": kind}
        relevant = {"plate": ("width", "height", "thickness", "corner_radius", "chamfer"),
                    "box": ("width", "height", "depth"), "sphere": ("radius",),
                    "bracket": ("width", "height", "depth", "wall", "throughhole_radius")}.get(kind, ())
        geometry.update({key: self.geometry_fields[key].value() for key in relevant})
        if kind == "plate":
            geometry["holes"] = [{key: float(self.holes.item(row, col).text()) for col, key in enumerate(("x", "y", "radius"))}
                                 for row in range(self.holes.rowCount())]
        if kind == "polygon":
            geometry["outer"] = strict_json(self.polygon_outer.toPlainText())
            geometry["holes"] = strict_json(self.polygon_holes.toPlainText())
            geometry["thickness"] = self.geometry_fields["thickness"].value()
        geometry["transform"] = {"translation": [field.value() for field in self.translation], "scale": self.scale.value()}
        project["geometry"] = geometry
        project["units"] = {"input": self.input_units.currentData(), "internal": "m"}
        project["mesh"] = {"size": self.mesh_size.value(), "min_size": self.min_size.value(), "order": self.order.currentData(),
                           "algorithm": self.algorithm.currentData(), "threads": self.calculation_threads.value(),
                           "max_elements": self.max_elements.value(), "recombine": self.recombine.isChecked()}
        project["repair"] = {"enabled": self.repair_enabled.isChecked(), "tolerance": self.repair_tolerance.value(),
                             "sew": True, "small_edges": True, "small_faces": False, "make_solids": self.make_solids.isChecked()}
        dimension = 3 if project["mode"] == "volume" else 2
        selected = [(dim, tag) for dim, tag in self.selected_entities() if dim == dimension]
        project["selection"] = {"scope": self.scope.currentData(), "dimension": dimension, "tags": [tag for _, tag in selected]}
        project["local_sizes"] = []
        for row in range(self.local_table.rowCount()):
            item = copy.deepcopy(self.local_table.item(row, 0).data(Qt.UserRole) or {})
            dimension_key = "dimension" if "dimension" in item and "dim" not in item else "dim"
            item[dimension_key] = int(self.local_table.item(row, 0).text())
            item["tag"] = int(self.local_table.item(row, 1).text())
            item["size"] = float(self.local_table.item(row, 2).text())
            project["local_sizes"].append(item)
        project["correspondences"] = [{"dimension": int(self.correspondence_table.item(row, 0).text()),
                                        "master": int(self.correspondence_table.item(row, 1).text()),
                                        "slave": int(self.correspondence_table.item(row, 2).text()),
                                        "translation": strict_json(self.correspondence_table.item(row, 3).text())}
                                       for row in range(self.correspondence_table.rowCount())]
        project["critical_zones"] = [copy.deepcopy(self.zone_table.item(row, 0).data(Qt.UserRole))
                                     for row in range(self.zone_table.rowCount())]
        policy = copy.deepcopy(project.get("quality_policy", {}))
        policy.update(minimum_quality=self.minimum_quality.value(), poor_threshold=self.poor_threshold.value(),
                      strict=self.strict_quality.isChecked())
        policy.pop("max_poor_elements", None)
        policy.pop("max_poor_fraction", None)
        if self.limit_poor_count.isChecked(): policy["max_poor_elements"] = self.max_poor_count.value()
        if self.limit_poor_fraction.isChecked(): policy["max_poor_fraction"] = self.max_poor_fraction.value() / 100
        project["quality_policy"] = policy
        project["learning"] = {**project.get("learning", {}), "max_trials": self.learning_trials.value(),
                               "epochs": self.learning_epochs.value(), "use_memory": self.use_memory.isChecked()}
        return project

    def selected_entities(self):
        return [tuple(item.data(0, Qt.UserRole)) for item in self.model_tree.selectedItems() if item.data(0, Qt.UserRole)]

    def tree_selection_changed(self):
        selected = self.selected_entities()
        self.viewport.set_selected_entities(selected)
        self.selection_label.setText(", ".join(f"{'Volume' if d == 3 else 'Face' if d == 2 else 'Courbe'} {t}" for d, t in selected) or "Aucune entité sélectionnée")

    def viewport_selected(self, dimension, tag):
        found = False
        def visit(item):
            nonlocal found
            if item.data(0, Qt.UserRole) == (dimension, tag):
                if not QApplication.keyboardModifiers() & Qt.ControlModifier: self.model_tree.clearSelection()
                item.setSelected(True)
                self.model_tree.scrollToItem(item)
                found = True
            for index in range(item.childCount()): visit(item.child(index))
        for index in range(self.model_tree.topLevelItemCount()): visit(self.model_tree.topLevelItem(index))
        if not found: self.set_status(f"Face {tag} : consultez le catalogue des entités après examen.")

    def add_local_sizes(self):
        selected = self.selected_entities()
        if not selected:
            self.set_status("Sélectionnez une face ou un volume dans le navigateur.")
            return
        for dim, tag in selected: self._append_local(dim, tag, self.local_size.value())

    def _append_local(self, dim, tag, size, *, metadata=None):
        row = self.local_table.rowCount()
        self.local_table.insertRow(row)
        for col, value in enumerate((dim, tag, size)):
            item = QTableWidgetItem(str(value))
            if col == 0 and metadata is not None: item.setData(Qt.UserRole, copy.deepcopy(metadata))
            self.local_table.setItem(row, col, item)

    def add_critical_zone(self):
        selected = self.selected_entities()
        if not selected:
            self.set_status("Examinez la pièce puis sélectionnez les entités de la zone à protéger.")
            return
        names = {self.zone_table.item(row, 0).data(Qt.UserRole).get("name") for row in range(self.zone_table.rowCount())}
        base_name = self.zone_name.text().strip() or self.zone_kind.currentText()
        dimensions = sorted({dimension for dimension, _ in selected})
        for dimension in dimensions:
            name = base_name if len(dimensions) == 1 else f"{base_name} · dimension {dimension}"
            original_name, number = name, 2
            while name in names:
                name = f"{original_name} ({number})"
                number += 1
            names.add(name)
            self._append_critical_zone({"name": name, "kind": self.zone_kind.currentData(), "dimension": dimension,
                                       "tags": sorted({tag for dim, tag in selected if dim == dimension}),
                                       "minimum_quality": self.zone_minimum.value(), "buffer": self.zone_buffer.value()})
        self.set_status("Zone protégée ajoutée. Ses critères seront vérifiés au prochain calcul.")

    def _append_critical_zone(self, zone):
        row = self.zone_table.rowCount()
        self.zone_table.insertRow(row)
        zone = copy.deepcopy(zone)
        dimension = zone.get("dimension", zone.get("dim", 2))
        values = (zone.get("name", "Zone sensible"), f"{dimension} : " + ", ".join(str(tag) for tag in zone.get("tags", [])),
                  f"{zone.get('minimum_quality', .35):.4g}", f"{zone.get('buffer', 0):.6g}")
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(zone.get("kind", "custom")))
            if column == 0: item.setData(Qt.UserRole, zone)
            self.zone_table.setItem(row, column, item)

    def remove_critical_zone(self):
        row = self.zone_table.currentRow()
        if row >= 0: self.zone_table.removeRow(row)

    def select_critical_zone(self):
        if self._loading or self.zone_table.currentRow() < 0: return
        item = self.zone_table.item(self.zone_table.currentRow(), 0)
        if item is None: return
        zone = item.data(Qt.UserRole) or {}
        desired = {(zone.get("dimension", zone.get("dim", 2)), tag) for tag in zone.get("tags", [])}
        self.model_tree.clearSelection()
        def select(item):
            if item.data(0, Qt.UserRole) in desired: item.setSelected(True)
            for index in range(item.childCount()): select(item.child(index))
        for index in range(self.model_tree.topLevelItemCount()): select(self.model_tree.topLevelItem(index))

    def add_correspondence(self):
        self._append_correspondence({"dimension": self.periodic_dimension.currentData(), "master": self.master.value(),
                                     "slave": self.slave.value(), "translation": [f.value() for f in self.periodic_translation]})

    def _append_correspondence(self, item):
        row = self.correspondence_table.rowCount()
        self.correspondence_table.insertRow(row)
        values = (item["dimension"], item["master"], item["slave"], json.dumps(item.get("translation", [0, 0, 0])))
        for col, value in enumerate(values): self.correspondence_table.setItem(row, col, QTableWidgetItem(str(value)))

    def _ensure_session(self):
        if self.session is None:
            from .native import NativeSession
            self.session = NativeSession(self.workspace)
        return self.session

    def _launch(self, function, message, *, export=False, training=False, memory=False, corpus=False):
        if self.calculation_thread is not None and self.calculation_thread.isRunning():
            self.set_status("Un calcul est déjà en cours.")
            return
        self.set_status(message)
        self.log.appendPlainText(message)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.properties.setEnabled(False)
        self.pages.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.remesh_button.setEnabled(False)
        self.train_button.setEnabled(False)
        self.learn_button.setEnabled(False)
        self.learn_ribbon_button.setEnabled(False)
        self.memory_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.calculation_thread = CalculationThread(function, self)
        callback = self._corpus_succeeded if corpus else self._memory_succeeded if memory else self._training_succeeded if training else self._export_succeeded if export else self._calculation_succeeded
        self.calculation_thread.succeeded.connect(callback)
        self.calculation_thread.failed.connect(self._calculation_failed)
        self.calculation_thread.finished.connect(self._calculation_finished)
        self.calculation_thread.start()

    def inspect_geometry(self):
        try:
            project, session = self.project_from_form(), self._ensure_session()
            self._pending_project = copy.deepcopy(project)
            self._pending_operation = "inspect"
            self._launch(lambda: session.inspect(project, source=self.source), "Examen de la géométrie par le noyau Rust…")
        except Exception as error: self._calculation_failed(str(error))

    def generate_mesh(self):
        try:
            project, session = self.project_from_form(), self._ensure_session()
            if project["selection"]["scope"] == "entities" and not project["selection"]["tags"]:
                raise ValueError("Sélectionnez les faces ou volumes à mailler dans le navigateur.")
            self._pending_project = copy.deepcopy(project)
            self._pending_operation = "mesh"
            use_advisor = self.use_guide.isChecked()
            self._launch(lambda: session.generate(project, source=self.source, use_advisor=use_advisor), "Génération du maillage dans le noyau Rust…")
        except Exception as error: self._calculation_failed(str(error))

    def remesh_selected(self):
        try:
            if not self.result or self._is_display_only():
                raise ValueError("Générez d'abord un maillage de calcul avant de remailler une zone. L'examen de la géométrie produit seulement un aperçu.")
            project, session = self.project_from_form(), self._ensure_session()
            project["selection"]["scope"] = "entities"
            if not project["selection"]["tags"]: raise ValueError("Sélectionnez une face ou un volume de la dimension du domaine.")
            self.scope.setCurrentIndex(self.scope.findData("entities"))
            self._pending_project = copy.deepcopy(project)
            self._pending_operation = "remesh"
            directory = Path(self.result["directory"])
            self._launch(lambda: session.remesh(project, source=self.source, previous_directory=directory), "Remaillage local et contrôle des interfaces conservées…")
        except Exception as error: self._calculation_failed(str(error))

    def cancel_calculation(self):
        if self.session is not None and hasattr(self.session, "cancel"):
            self.session.cancel()
            self.set_status("Annulation demandée au noyau…")

    def train_guide(self):
        try:
            project = self.project_from_form()
            session = self._ensure_session()
            self._training_requested_repair = bool(project.get("repair", {}).get("enabled"))
            self._launch(lambda: session.train(project=project), "Évaluation des stratégies et entraînement du guide dans le noyau Rust…", training=True)
        except Exception as error:
            self._calculation_failed(str(error))

    def learn_current_piece(self):
        try:
            project, session = self.project_from_form(), self._ensure_session()
            if project["selection"]["scope"] == "entities" and not project["selection"]["tags"]:
                raise ValueError("Sélectionnez les faces ou volumes à mailler avant de lancer la recherche.")
            if project["geometry"]["kind"] == "import" and self.source is None:
                raise ValueError("Joignez le fichier de la pièce importée avant de lancer son apprentissage.")
            if not hasattr(session, "learn"):
                raise RuntimeError("Le noyau installé ne propose pas encore l'apprentissage sur la pièce. Relancez l'installation de cette version.")
            self._pending_project = copy.deepcopy(project)
            self._pending_operation = "learn"
            source = self.source
            max_trials, epochs, use_memory = self.learning_trials.value(), self.learning_epochs.value(), self.use_memory.isChecked()
            max_seconds = project.get("learning", {}).get("max_seconds", 240)
            self._launch(lambda: session.learn(project, source=source, max_trials=max_trials, epochs=epochs,
                                               use_memory=use_memory, max_seconds=max_seconds),
                         f"Recherche sur la pièce ouverte · {max_trials} essais au maximum, avec contrôle des zones critiques…")
        except Exception as error:
            self._calculation_failed(str(error))

    def show_memory(self):
        try:
            session = self._ensure_session()
            if not hasattr(session, "memory_info"):
                self.set_status("La mémoire sera disponible après l'installation du noyau de cette version.")
                return
            self._launch(session.memory_info, "Lecture de la mémoire d'apprentissage…", memory=True)
        except Exception as error:
            self._calculation_failed(str(error))

    def choose_corpus_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Dossier du corpus", self.corpus_directory.text())
        if directory:
            self.corpus_directory.setText(directory)

    def _start_corpus_operation(self, operation, method, message, **kwargs):
        """Collect controls and delegate the entire corpus operation to Rust."""
        try:
            if self.calculation_thread is not None and self.calculation_thread.isRunning():
                self.set_status("Un calcul est déjà en cours.")
                return
            text = self.corpus_directory.text().strip()
            if not text:
                raise ValueError("Choisissez un dossier pour le corpus.")
            directory = Path(text).expanduser()
            session = self._ensure_session()
            native_method = getattr(session, method, None)
            if native_method is None:
                raise RuntimeError("Le noyau installé ne propose pas les corpus par lots. Relancez l'installation de cette version.")
            self._pending_corpus_operation = operation
            self._launch(lambda: native_method(corpus_dir=directory, **kwargs), message, corpus=True)
        except Exception as error:
            self._calculation_failed(str(error))

    def plan_corpus(self):
        self._start_corpus_operation("corpus_plan", "corpus_plan", "Création du plan du corpus · aucun calcul de maillage n'est lancé…",
            structured=self.corpus_structured.value(), random=self.corpus_random.value(), seed=self.corpus_seed.value(),
            strategies=self.corpus_strategies.value(), shard_size=1000)

    def run_corpus_batch(self):
        self._start_corpus_operation("corpus_run", "corpus_run", "Calcul du prochain lot dans Rust · reprise des cas non terminés…",
            limit=self.corpus_batch_cases.value(), max_seconds=self.corpus_seconds.value(),
            retain_meshes=self.corpus_keep_meshes.isChecked())

    def show_corpus_status(self):
        self._start_corpus_operation("corpus_status", "corpus_status", "Lecture de l'état du corpus…")

    def train_corpus_batches(self):
        self._start_corpus_operation("corpus_train", "corpus_train", "Entraînement par mini-lots dans Rust · le test final reste réservé…",
            epochs=self.corpus_epochs.value(), batch_size=self.corpus_training_batch.value(),
            max_records=self.corpus_training_records.value(), max_seconds=self.corpus_seconds.value())

    def _corpus_succeeded(self, result):
        report = result.get("report", result)
        if not isinstance(report, dict):
            self._calculation_failed("Le noyau a retourné un rapport de corpus invalide.")
            return
        operation = report.get("operation", self._pending_corpus_operation)
        training = operation == "corpus_train" or report.get("schema") == "atelier.corpus.training_report.v1"
        if training:
            text = (f"Entraînement par lots · passages terminés : {report.get('epochs_completed', '—')} "
                    f"sur {report.get('epochs_requested', '—')}. "
                    f"{report.get('batches_completed', '—')} mini-lots traités.\n")
            text += "Classement du réseau activé après validation." if report.get("deploy_enabled") else "Classement du réseau inactif : aucun avantage suffisant démontré par la validation."
            splits = {"train": report.get("training_cases", "—"), "validation": report.get("validation_cases", "—"),
                      "test": report.get("test_cases", "—")}
            if report.get("warm_started") or report.get("resumed"):
                text += "\nLes poids ou le point de reprise précédents ont été réutilisés."
            if report.get("stopped_by_budget"):
                text += "\nBudget atteint ; le point de reprise est enregistré."
            test = report.get("test", {})
            if isinstance(test, dict) and test.get("cases") is not None:
                text += f"\nTest final distinct : {test['cases']} configurations évaluées après sélection du modèle."
        else:
            text = (f"Objectif : {report.get('planned_total', '—')} configurations. "
                    f"Traitées : {report.get('completed_cases', '—')}.\n"
                    f"Appels au moteur : {report.get('backend_evaluations', '—')}.\n"
                    f"Stratégies acceptées : {report.get('accepted_actions', '—')} · "
                    f"refusées ou échouées : {report.get('failed_actions', '—')}.")
            if report.get("generated_cases") is not None:
                text += f"\nConfigurations générées : {report['generated_cases']}."
            if report.get("meshed_cases") is not None:
                text += f" Au moins un maillage produit : {report['meshed_cases']}."
            if report.get("admissible_cases") is not None:
                text += f" Configurations admissibles : {report['admissible_cases']}."
            if operation == "corpus_plan":
                text += "\nLe plan est enregistré. Le calcul ne démarre qu'avec le bouton de lot."
            if report.get("budget_stopped"):
                text += "\nLot interrompu au budget ; les cas terminés sont conservés pour la reprise."
            if report.get("done"):
                text += "\nToutes les configurations de ce plan ont été traitées."
            splits = report.get("split_counts", {})
        self.corpus_partitions.setRowCount(0)
        if isinstance(splits, dict):
            for key, label in (("train", "Entraînement"), ("validation", "Validation"), ("test", "Test final")):
                row = self.corpus_partitions.rowCount()
                self.corpus_partitions.insertRow(row)
                self.corpus_partitions.setItem(row, 0, QTableWidgetItem(label))
                self.corpus_partitions.setItem(row, 1, QTableWidgetItem(str(splits.get(key, "—"))))
        if report.get("checkpoint_may_lag"):
            text += "\nUn lot est en cours ; les compteurs seront actualisés au prochain point de reprise."
        self.corpus_summary.setText(text)
        self.log.appendPlainText("Rapport du corpus :\n" + json.dumps(report, ensure_ascii=False, indent=2))
        self.set_status("Opération sur le corpus terminée. Le maillage affiché est conservé.")

    def _memory_succeeded(self, result):
        info = result.get("memory", result.get("report", result))
        contexts = info.get("contexts", info.get("history_contexts", info.get("geometries", info.get("geometry_count", "—"))))
        observations = info.get("observations", info.get("history_observations", info.get("experiences", info.get("total_experiences", "—"))))
        models = info.get("models", info.get("model_versions", info.get("model_version", "—")))
        def count(value): return len(value) if isinstance(value, (list, dict)) else value
        text = f"Mémoire disponible : {count(contexts)} contextes · {count(observations)} essais enregistrés · modèles : {count(models)}."
        self.experience_summary.setText(text)
        self.log.appendPlainText("Mémoire d'apprentissage :\n" + json.dumps(info, ensure_ascii=False, indent=2))
        self.set_status(text)

    def _update_experience(self, experience):
        if not isinstance(experience, dict) or not experience: return
        self.experience_history.setRowCount(0)
        trials = experience.get("trials", [])
        for index, trial in enumerate(trials):
            if not isinstance(trial, dict): continue
            row = self.experience_history.rowCount()
            self.experience_history.insertRow(row)
            quality = trial.get("quality", {})
            minimum = quality.get("minimum") if isinstance(quality, dict) else quality
            accepted = bool(trial.get("accepted", False))
            state = "Accepté" if accepted else "Hors critères" if trial.get("success", False) else "Échec"
            label = {"automatic": "Auto", "delaunay": "Delaunay", "frontal": "Frontal"}.get(trial.get("algorithm"), trial.get("algorithm", "—"))
            if isinstance(trial.get("size"), (int, float)): label += f" · h={trial['size']:.4g}"
            values = (str(trial.get("index", index + 1)), label,
                      f"{minimum:.4g}" if isinstance(minimum, (int, float)) else "—", state)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if trial.get("error"): item.setToolTip(str(trial["error"]))
                if column == 3: item.setForeground(QColor("#267453" if accepted else "#9a5c20"))
                self.experience_history.setItem(row, column, item)
        reused = bool(experience.get("memory_reused", experience.get("reuse", False)))
        count = experience.get("executed_trials", len(trials))
        learning = experience.get("learning", {})
        text = f"{count} essais exécutés. " + ("Une recette mémorisée a été réévaluée. " if reused else "Nouvelle recherche enregistrée. ")
        if experience.get("history_contexts") is not None:
            text += f"Mémoire : {experience['history_contexts']} contextes, {experience.get('history_observations', '—')} essais. "
        if isinstance(learning, dict) and learning:
            text += "Guide cumulatif : " + ("utilisation validée." if learning.get("deploy_enabled") else "conservé, sans avantage validé suffisant pour piloter les essais.")
            if learning.get("previous_model_rejected"):
                text += "\nL'ancien réseau est incompatible avec cette représentation ; un réseau compatible a été initialisé. Les recettes restent conservées et revalidées."
        migration = experience.get("representation_migration", {})
        if isinstance(migration, dict) and migration.get("message"):
            text += "\n" + str(migration["message"])
        self.experience_summary.setText(text)

    def _training_succeeded(self, result):
        report = result.get("report", result)
        reserved = report.get("validation", {})
        guide = report.get("validation_guide_reward", reserved.get("guide_reward"))
        fixed = report.get("validation_fixed_reward", reserved.get("fixed_reward"))
        enabled = bool(report.get("deploy_enabled", False))
        comparison = ""
        if isinstance(guide, (int, float)) and isinstance(fixed, (int, float)):
            comparison = f"Validation : guide {guide:.5f} · stratégie fixe {fixed:.5f}. "
        if enabled:
            self.learning_summary.setText(comparison + "Avantage mesuré : le guide peut être utilisé pour le prochain maillage.")
        else:
            self.use_guide.setChecked(False)
            self.learning_summary.setText(comparison + "Guide inactif : aucun avantage validé suffisant. L'algorithme demandé reste utilisé.")
        if getattr(self, "_training_requested_repair", False):
            self.learning_summary.setText(self.learning_summary.text() + "\nLes essais du guide sont réalisés sans réparation. Le modèle ne s'appliquera pas à une pièce avec réparation activée.")
        self.log.appendPlainText("Rapport d'apprentissage :\n" + json.dumps(report, ensure_ascii=False, indent=2))
        self.set_status("Entraînement terminé. Le maillage et ses réglages calculés sont conservés.")

    def _calculation_succeeded(self, result):
        report = result.get("report", result)
        experience = report.get("experience", {})
        chosen = report.get("selected_project", experience.get("chosen_project") if isinstance(experience, dict) else None)
        if isinstance(chosen, dict):
            operation = self._pending_operation
            self.load_project(chosen, self.source)
            self._pending_project = copy.deepcopy(chosen)
            self._pending_operation = operation
        self.result = result
        self.result_project = copy.deepcopy(self._pending_project)
        self._result_operation = self._pending_operation
        preview = result.get("preview", {})
        if isinstance(preview, (str, Path)):
            preview = strict_json(Path(preview).read_bytes())
        if preview:
            self.viewport.set_preview(preview, label="Aperçu géométrique · non exportable pour le calcul" if self._is_display_only() else "Maillage de calcul")
        self._update_catalog(report)
        requested_selection = (self.result_project or {}).get("selection", {})
        if requested_selection.get("scope") == "entities":
            desired = {(requested_selection.get("dimension"), tag) for tag in requested_selection.get("tags", [])}
            def restore(item):
                if item.data(0, Qt.UserRole) in desired: item.setSelected(True)
                for index in range(item.childCount()): restore(item.child(index))
            for index in range(self.model_tree.topLevelItemCount()): restore(self.model_tree.topLevelItem(index))
        quality = report.get("quality", {})
        minimum = quality.get("minimum", quality.get("min_sicn", quality.get("min_scaled_jacobian")))
        summary = f"{report.get('nodes', '—')} nœuds · {report.get('elements', '—')} éléments"
        if isinstance(minimum, (int, float)): summary += f"\nQualité minimale : {minimum:.5g}"
        if quality.get("poor_count") is not None:
            summary += f"\nÉléments sous le seuil : {quality['poor_count']}"
            if isinstance(quality.get("poor_fraction"), (int, float)):
                summary += f" ({100 * quality['poor_fraction']:.3g} %)"
        if quality.get("accepted") is not None:
            summary += "\nCritères respectés" if quality["accepted"] else "\nCritères non respectés · consulter le diagnostic"
        self.zone_quality_table.setRowCount(0)
        for zone in quality.get("critical_zones", []):
            row = self.zone_quality_table.rowCount()
            self.zone_quality_table.insertRow(row)
            minimum = zone.get("minimum")
            values = (zone.get("name", "Zone"), zone.get("elements", 0),
                      f"{minimum:.4g}" if isinstance(minimum, (int, float)) else "—", zone.get("below_minimum", 0))
            for column, value in enumerate(values): self.zone_quality_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._update_experience(experience)
        self.quality_label.setText(summary)
        self.export_summary.setText("Aperçu géométrique uniquement. Générer le maillage de calcul avant l'exportation." if self._is_display_only()
                                   else summary + "\nUnité interne : mètre. L'export effectue la conversion demandée.")
        self.diagnostics.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.log.appendPlainText(summary)
        self.set_status("Opération terminée. Examiner la sélection, la qualité et les avertissements.")

    def _update_catalog(self, report):
        self.model_tree.clear()
        root = QTreeWidgetItem([self.name.text()])
        self.model_tree.addTopLevelItem(root)
        catalog = report.get("entity_catalog", report.get("entities", []))
        if not isinstance(catalog, list): catalog = []
        categories = {}
        for entry in catalog:
            if not isinstance(entry, dict): continue
            dim, tag = entry.get("dim", entry.get("dimension")), entry.get("tag")
            if not isinstance(dim, int) or not isinstance(tag, int): continue
            if dim not in categories:
                label = {0: "Sommets", 1: "Courbes", 2: "Faces", 3: "Volumes"}.get(dim, "Entités")
                categories[dim] = QTreeWidgetItem(root, [label])
            item = QTreeWidgetItem(categories[dim], [entry.get("name") or f"{'Volume' if dim == 3 else 'Face' if dim == 2 else 'Courbe'} {tag}"])
            item.setData(0, Qt.UserRole, (dim, tag))
            if entry.get("bbox"): item.setToolTip(0, "Boîte englobante en mètres : " + str(entry["bbox"]))
        self.model_tree.expandToDepth(1)

    def _calculation_failed(self, message):
        if self._pending_corpus_operation is not None:
            self.corpus_summary.setText("Opération sur le corpus interrompue : " + message)
        self.log.appendPlainText("Échec : " + message)
        self.log.show()
        self.set_status(message)

    def _calculation_finished(self):
        self.progress.hide()
        self.properties.setEnabled(True)
        self.pages.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.remesh_button.setEnabled(True)
        self.train_button.setEnabled(True)
        self.learn_button.setEnabled(True)
        self.learn_ribbon_button.setEnabled(True)
        self.memory_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._pending_corpus_operation = None
        if self._closing: self.close()

    def _export_succeeded(self, result):
        self.log.appendPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.set_status("Export terminé ; vérifier le compte rendu et les fichiers produits.")

    def export_mesh(self):
        try:
            if not self.result: raise ValueError("Générez un maillage avant l'exportation.")
            if self._is_display_only():
                raise ValueError("Cet aperçu sert à examiner la géométrie. Générez un maillage de calcul à la page Maillage avant l'exportation.")
            if self.result.get("report", self.result).get("quality", {}).get("accepted") is False:
                raise ValueError("Les critères de qualité ne sont pas respectés. Corrigez la discrétisation ou les critères et relancez le calcul avant l'exportation.")
            if not self._result_matches_current_project():
                raise ValueError("La géométrie, les unités ou les conditions ont changé depuis le calcul. Régénérez le maillage avant l'exportation.")
            destination = QFileDialog.getExistingDirectory(self, "Dossier d'exportation")
            if not destination: return
            session = self._ensure_session()
            directory = Path(self.result["directory"])
            target, fmt, units = Path(destination), self.format.currentData(), self.export_units.currentData()
            planar = self.mode.currentData() == "planar"
            self._launch(lambda: session.export(directory, target, fmt, units=units, planar=planar), "Conversion des unités et préparation de l'export…", export=True)
        except Exception as error: self._calculation_failed(str(error))

    def import_geometry(self):
        if self.calculation_thread is not None and self.calculation_thread.isRunning():
            self.set_status("Attendez la fin du calcul ou arrêtez-le avant d'importer une autre pièce.")
            return
        from .import_registry import file_dialog_filter, MESH_READERS, source_extension
        filename, _ = QFileDialog.getOpenFileName(self, "Importer une pièce", "", file_dialog_filter())
        if not filename: return
        project = self.project_from_form()
        project["name"] = Path(filename).stem
        project["geometry"] = {"kind": "import"}
        if source_extension(filename) in set(MESH_READERS) | {".stl", ".unv", ".3mf"}: project["mode"] = "surface"
        self.load_project(project, Path(filename))
        self.set_status("Vérifiez l'unité et le domaine, puis examinez la géométrie. Les formats propriétaires demandent une passerelle locale configurée ; les maillages sont reconstruits à partir de leur peau.")

    def load_example(self):
        if self.calculation_thread is not None and self.calculation_thread.isRunning():
            self.set_status("Attendez la fin du calcul ou arrêtez-le avant de changer de pièce.")
            return
        item = self._example_items[self.examples.currentIndex()]
        project = copy.deepcopy(item["project"])
        project["units"] = {"input": self.input_units.currentData() or "mm", "internal": "m"}
        self.load_project(project)
        self.set_status("Exemple chargé. Examiner la géométrie pour obtenir les entités sélectionnables.")

    def save_project(self):
        try:
            project = self.project_from_form()
            filename, _ = QFileDialog.getSaveFileName(self, "Enregistrer le projet portable", project["name"] + ".amp", "Projet Atelier Mailleur (*.amp)")
            if not filename: return
            path = Path(filename)
            if path.suffix.lower() != ".amp": path = path.with_suffix(".amp")
            path.write_bytes(save_portable_project(project, self.source))
            self.set_status("Projet enregistré avec sa source et ses réglages.")
        except Exception as error: self._calculation_failed(str(error))

    def open_project(self):
        if self.calculation_thread is not None and self.calculation_thread.isRunning():
            self.set_status("Attendez la fin du calcul ou arrêtez-le avant d'ouvrir un autre projet.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Ouvrir un projet", "", "Projet Atelier Mailleur (*.amp);;Projet JSON (*.json)")
        if not filename: return
        try:
            if Path(filename).suffix.lower() == ".amp":
                folder = Path(tempfile.mkdtemp(prefix="projet_", dir=self.workspace))
                loaded = load_portable_project(Path(filename).read_bytes(), folder)
                self.load_project(loaded["project"], loaded["source"])
            else:
                project = strict_json(Path(filename).read_bytes())
                self.load_project(project)
            self.set_status("Projet ouvert. Vérifiez l'unité avant de reprendre les calculs.")
        except Exception as error: self._calculation_failed(str(error))

    def toggle_edges(self, checked):
        self.viewport.show_edges = checked
        self.viewport.update()

    def _is_display_only(self):
        if not self.result: return False
        report = self.result.get("report", self.result)
        return bool(self.result.get("display_only") or report.get("display_only")
                    or report.get("operation") == "inspect" or self._result_operation == "inspect")

    @staticmethod
    def _mesh_identity(project):
        value = copy.deepcopy(project or {})
        value.pop("name", None)
        value.pop("learning", None)
        selection = value.get("selection", {})
        if selection.get("scope") == "all": selection["tags"] = []
        return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)

    def _result_matches_current_project(self):
        if self.result_project is None: return False
        return self._mesh_identity(self.project_from_form()) == self._mesh_identity(self.result_project)

    def closeEvent(self, event):
        if self.calculation_thread is not None and self.calculation_thread.isRunning():
            self._closing = True
            self.cancel_calculation()
            event.ignore()
        else:
            event.accept()


def main(argv=None):
    app = QApplication.instance() or QApplication(argv or [])
    app.setApplicationName("Atelier Mailleur")
    app.setOrganizationName("AtelierMailleur")
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSize(11)
    app.setFont(font)
    window = AtelierWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
