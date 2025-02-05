import re
from collections import defaultdict
from concurrent.futures import Future
from typing import Any, Dict, List, Optional, Union

from aqt import mw
from aqt.clayout import CardLayout
from aqt.qt import QWidget
from aqt.utils import askUser, showInfo, tooltip

from ..ankiaddonconfig import ConfigManager, ConfigWindow
from ..ankiaddonconfig.window import ConfigLayout
from ..constants import ANKIHUB_NOTETYPE_RE, NOTETYPE_COPY_RE
from ..notetype_setting import NotetypeSetting, NotetypeSettingException
from ..notetype_setting_definitions import (
    anking_notetype_model,
    anking_notetype_names,
    configurable_fields_for_notetype,
    general_settings,
    general_settings_defaults_dict,
    setting_configs,
)
from ..utils import update_notetype_to_newest_version
from .anking_widgets import AnkingIconsLayout, AnkiPalaceLayout, GithubLinkLayout
from .extra_notetype_versions import handle_extra_notetype_versions

try:
    from anki.models import NotetypeDict  # pylint: disable=unused-import
except:
    pass


def ntss_for_model(model: "NotetypeDict") -> List[NotetypeSetting]:

    # returns all nts that are present on the notetype
    result = []
    for setting_config in setting_configs.values():
        nts = NotetypeSetting.from_config(setting_config)
        if nts.is_present(model):
            result.append(nts)

    return result


def general_ntss() -> List[NotetypeSetting]:
    result = []
    for setting_name in general_settings:
        result.append(NotetypeSetting.from_config(setting_configs[setting_name]))
    return result


class NotetypesConfigWindow:

    window: Optional[ConfigWindow] = None

    def __init__(self, clayout_: CardLayout = None):

        # code in this class assumes that if bool(clayout) is true, clayout.model contains
        # an anking notetype model
        self.clayout = None
        if clayout_:
            if clayout_.model["name"] in anking_notetype_names():
                self.clayout = clayout_
            elif (
                clayout_.model["name"] in self._all_supported_note_types()
                and (base_name := self._base_name(clayout_.model["name"])) is not None
            ):
                showInfo(
                    "When you edit this note type here you won't see the changes in the preview.\n\n"
                    f'You can edit "{base_name}" instead and the changes '
                    "will be applied to this note type too."
                )

        self.conf = None
        self.last_general_ntss: Union[List[NotetypeSetting], None] = None

    def open(self):

        handle_extra_notetype_versions()

        # dont open another window if one is already open
        if self.__class__.window:
            is_open = True
            # when the window is closed c++ deletes the qdialog and calling methods
            # of the window fails with a RuntimeError
            try:
                self.window.isVisible()
            except RuntimeError:
                is_open = False

            if is_open:
                self.window.activateWindow()
                self.window.raise_()
                return

        # ankiaddonconfig's ConfigManager is used here in a way that is not intended
        # the save functionality gets overwritten and nothing gets saved to the Anki
        # addon config
        # the config is populated at the start with the current setting values parsed
        # from the notetype and then used to update the settings
        self.conf = ConfigManager()

        self._read_in_settings()

        # add general tab
        self.conf.add_config_tab(lambda window: self._add_general_tab(window))

        # setup tabs for all notetypes
        for notetype_name in sorted(anking_notetype_names()):
            self.conf.add_config_tab(
                lambda window, notetype_name=notetype_name: self._add_notetype_settings_tab(
                    notetype_name, window
                )
            )

        # setup live update of clayout model on changes
        def live_update_clayout_model(key: str, _: Any):
            model = self.clayout.model
            notetype_name, setting_name = key.split(".")
            if notetype_name != self.clayout.model["name"]:
                return

            nts = NotetypeSetting.from_config(setting_configs[setting_name])
            self._safe_update_model_settings(
                model=model, model_base_name=model["name"], ntss=[nts]
            )

            self._update_clayout_model(model)

        if self.clayout:
            self.conf.on_change(live_update_clayout_model)

        # change window settings, overwrite on_save, setup notetype updates
        self.conf.on_window_open(self._setup_window_settings)

        # open the config window
        if self.clayout:
            self.conf.open_config(self.clayout)
        else:
            self.conf.open_config()

    def _setup_window_settings(self, window: ConfigWindow):
        self.__class__.window = window
        window.setWindowTitle("AnKing Note Types")
        window.setMinimumHeight(500)
        window.setMinimumWidth(500)

        # overwrite on_save function
        def on_save(window: ConfigWindow):
            self._apply_setting_changes_for_all_notetypes()
            window.close()

        window.save_btn.clicked.disconnect()  # type: ignore
        window.save_btn.clicked.connect(lambda: on_save(window))  # type: ignore

        if self.clayout:
            self._set_active_tab(self.clayout.model["name"])

        # add anking links layouts
        widget = QWidget()
        window.main_layout.insertWidget(0, widget)
        AnkingIconsLayout(widget)

        widget = QWidget()
        window.main_layout.addWidget(widget)
        AnkiPalaceLayout(widget)

        window.main_layout.addSpacing(10)
        widget = QWidget()
        window.main_layout.addWidget(widget)
        GithubLinkLayout(
            widget, href="https://github.com/AnKingMed/AnKing-Note-Types/issues"
        )

    # tabs and NotetypeSettings (ntss)
    def _add_notetype_settings_tab(
        self,
        notetype_name: str,
        window: ConfigWindow,
    ):
        if self.clayout and self.clayout.model["name"] == notetype_name:
            model = self.clayout.model
        else:
            model = mw.col.models.by_name(notetype_name)  # type: ignore

        tab = window.add_tab(notetype_name)

        if model:
            ntss = ntss_for_model(model)
            ordered_ntss = self._adjust_configurable_field_nts_order(
                ntss, notetype_name
            )
            scroll = tab.scroll_layout()
            self._add_nts_widgets_to_layout(scroll, ordered_ntss, model)
            scroll.stretch()

            layout = tab.hlayout()
            layout.button(
                "Reset",
                on_click=lambda: self._reset_notetype_and_reload_ui(model),
            )
            layout.stretch()
        else:
            tab.text("The notetype is not in the collection.")
            tab.stretch()

            tab.button(
                "Import",
                on_click=lambda: self._import_notetype_and_reload_tab(notetype_name),
            )

    def _add_general_tab(self, window: ConfigWindow):
        tab = window.add_tab("General")

        prev_ntss = self.last_general_ntss
        self.last_general_ntss = ntss = general_ntss()

        scroll = tab.scroll_layout()
        self._add_nts_widgets_to_layout(scroll, ntss, None, general=True)
        scroll.stretch()

        if prev_ntss:
            for nts in prev_ntss:
                nts.unregister_general_setting(tab.conf)

        for nts in ntss:
            nts.register_general_setting(tab.conf)

        tab.space(10)
        tab.text(
            "Changes made here will be applied to all note types that have this setting",
            bold=True,
            multiline=True,
        )
        tab.space(10)

        update_btn = tab.button(
            "Update notetypes",
            on_click=self._update_all_notetypes_to_newest_version_and_reload_ui,
        )

        if self.models_with_available_updates():
            tab.text("New versions of notetypes are available!")
        else:
            update_btn.setDisabled(True)

    def _add_nts_widgets_to_layout(
        self,
        layout: ConfigLayout,
        ntss: List[NotetypeSetting],
        model: "NotetypeDict",
        general=False,
    ) -> None:

        if general:
            assert model is None

        nts_to_section = {
            nts: section_name
            for nts in ntss
            if (section_name := nts.config.get("section", None))
        }

        section_to_ntss: Dict[str, List[NotetypeSetting]] = defaultdict(lambda: [])
        for nts, section in nts_to_section.items():
            section_to_ntss[section].append(nts)

        for section_name, section_ntss in sorted(section_to_ntss.items()):
            section = layout.collapsible_section(section_name)
            for nts in section_ntss:
                if general:
                    nts.add_widget_to_general_config_layout(section)
                else:
                    nts.add_widget_to_config_layout(section, model)
                section.space(7)
            layout.hseparator()
            layout.space(10)

        other_ntss: List[NotetypeSetting] = [
            nts for nts in ntss if nts not in nts_to_section.keys()
        ]
        for nts in other_ntss:
            if general:
                nts.add_widget_to_general_config_layout(layout)
            else:
                nts.add_widget_to_config_layout(layout, model)
            layout.space(7)

    def _adjust_configurable_field_nts_order(
        self, ntss: List[NotetypeSetting], notetype_name: str
    ) -> List[NotetypeSetting]:
        # adjusts the order of the hint button settings to be the same as
        # on the original anking card
        # it would probably be better to check the order of the buttons on the current
        # version of the card, not the original one

        field_ntss = [
            nts for nts in ntss if nts.config.get("configurable_field_name", False)
        ]
        ordered_field_names = configurable_fields_for_notetype(notetype_name)
        ordered_field_ntss = sorted(
            field_ntss,
            key=lambda nts: (
                ordered_field_names.index(name)
                if (name := nts.config["configurable_field_name"])
                in ordered_field_names
                else -1  # can happen because of different quotes in template versions
            ),
        )

        other_ntss = [nts for nts in ntss if nts not in field_ntss]
        return other_ntss + ordered_field_ntss

    # tab actions
    def _set_active_tab(self, tab_name: str) -> None:
        tab_widget = self.window.main_tab
        tab_widget.setCurrentIndex(self._get_tab_idx_by_name(tab_name))

    def _reload_tab(self, tab_name: str) -> None:
        tab_widget = self.window.main_tab
        index = self._get_tab_idx_by_name(tab_name)
        tab_widget.removeTab(index)

        if tab_name == "General":
            self._add_general_tab(self.window)
        else:
            notetype_name = tab_name
            self._add_notetype_settings_tab(notetype_name, self.window)
            # inserting the tab at its index or moving it to it after adding doesn't work for
            # some reason
            # tab_widget.tabBar().move(tab_widget.tabBar().count()-1, index)

            self._read_in_settings()

        self.window.update_widgets()
        self._set_active_tab(tab_name)

    def _get_tab_idx_by_name(self, tab_name: str) -> int:
        tab_widget = self.window.main_tab
        return next(
            (
                index
                for index in range(tab_widget.count())
                if tab_name == tab_widget.tabText(index)
            ),
            None,
        )

    # reset / update / import notetypes
    # note: these actions can be called by clicking their buttons and will modify mw.col.models regardless
    # of whether the Save button is pressed after that
    def _reset_notetype_and_reload_ui(self, model: "NotetypeDict"):
        if not askUser(
            f"Do you really want to reset the <b>{model['name']}</b> notetype to its default form?<br><br>"
            "After doing this Anki will require a full sync on the next synchronization with AnkiWeb. "
            "Make sure to synchronize unsynchronized changes from other devices first.",
            defaultno=True,
        ):
            return

        for model_version in self._notetype_versions(model["name"]):
            update_notetype_to_newest_version(model_version, model["name"])
            mw.col.models.update_dict(model_version)  # type: ignore

        if self.clayout:
            self._update_clayout_model(model)

        self._reload_tab(model["name"])

        tooltip("Notetype was reset", parent=self.window, period=1200)

    def _update_all_notetypes_to_newest_version_and_reload_ui(self):
        if not askUser(
            "Do you really want to update the note types? Settings will be kept.<br><br>After doing this Anki "
            "will require a full sync on the next synchronization with AnkiWeb. Make sure to synchronize "
            "unsynchronized changes from other devices first.",
            defaultno=True,
        ):
            return

        def task():

            to_be_updated = self.models_with_available_updates()

            for model in to_be_updated:
                for model_version in self._notetype_versions(model["name"]):
                    update_notetype_to_newest_version(model_version, model["name"])

                    # restore the values from before the update for the settings that exist in both versions
                    self._safe_update_model_settings(
                        model=model_version,
                        model_base_name=model["name"],
                        ntss=ntss_for_model(model_version),
                        show_tooltip_on_exception=False,
                    )

            return to_be_updated

        def on_done(updated_models_fut: Future):
            updated = updated_models_fut.result()
            if updated is None:
                tooltip(
                    "An error occured while updating the notetypes, old notetypes are kept",
                    parent=self.window,
                    period=1200,
                )
                return

            for model in updated:
                mw.col.models.update_dict(model)  # type: ignore
                if self.clayout and model["name"] == self.clayout.model["name"]:
                    self._update_clayout_model(model)

            self._reload_tab("General")
            for model in sorted(updated, key=lambda m: m["name"]):
                self._reload_tab(model["name"])

            self._set_active_tab("General")

            tooltip("Note types were updated", parent=self.window, period=1200)

        mw.taskman.with_progress(
            parent=self.window,
            label="Updating note types...",
            task=task,
            on_done=on_done,
            immediate=True,
        )

    @classmethod
    def _new_notetype_version_available(cls, model: "NotetypeDict"):
        current_version = cls.model_version(model)
        newest_version = cls.model_version(anking_notetype_model(model["name"]))
        return current_version != newest_version

    @classmethod
    def model_version(cls, model):
        front = model["tmpls"][0]["qfmt"]
        m = re.match(r"<!-- version ([\w\d]+) -->\n", front)
        if not m:
            return None
        return m.group(1)

    @classmethod
    def models_with_available_updates(cls):
        return [
            model
            for name in anking_notetype_names()
            if (model := mw.col.models.by_name(name)) is not None
            and cls._new_notetype_version_available(model)
        ]

    def _import_notetype_and_reload_tab(self, notetype_name: str) -> None:
        self._import_notetype(notetype_name)
        self._reload_tab(notetype_name)

    def _import_notetype(self, notetype_name: str) -> None:
        model = anking_notetype_model(notetype_name)
        model["id"] = 0
        mw.col.models.add_dict(model)  # type: ignore

    # read / write notetype settings
    # changes to settings will be written to mw.col.models when the Save button is pressed
    # (on the add-ons' dialog or in Anki's note type manager window)
    # this is done by _apply_setting_changes_for_all_notetypes
    def _read_in_settings(self):

        # read in settings from notetypes and general ones into config
        self._read_in_settings_from_notetypes()
        self._read_in_general_settings()

    def _read_in_settings_from_notetypes(self):
        error_msg = ""
        for notetype_name in anking_notetype_names():

            if self.clayout and notetype_name == self.clayout.model["name"]:
                # if in live preview mode read in current not confirmed settings
                model = self.clayout.model
            else:
                model = mw.col.models.by_name(notetype_name)

            if not model:
                continue
            for nts in ntss_for_model(model):
                try:
                    self.conf[nts.key(notetype_name)] = nts.setting_value(model)
                except NotetypeSettingException as e:
                    error_msg += f"failed parsing {notetype_name}:\n{str(e)}\n\n"

        if error_msg:
            showInfo(error_msg)

    def _read_in_general_settings(self):

        # read in default values
        for setting_name, value in general_settings_defaults_dict().items():
            self.conf.set(f"general.{setting_name}", value, on_change_trigger=False)

        # if all notetypes that have a nts have the same value set the value to it
        models_by_nts: Dict[NotetypeSetting, "NotetypeDict"] = defaultdict(lambda: [])
        for notetype_name in anking_notetype_names():
            model = mw.col.models.by_name(notetype_name)
            if not model:
                continue

            ntss = ntss_for_model(model)
            for nts in ntss:
                models_by_nts[nts].append(model)

        for nts, models in models_by_nts.items():
            try:
                setting_value = nts.setting_value(models[0]) if models else None
                if all(setting_value == nts.setting_value(model) for model in models):
                    self.conf.set(
                        f"general.{nts.name()}", setting_value, on_change_trigger=False
                    )
            except NotetypeSettingException:
                pass

    def _safe_update_model_settings(
        self,
        model: "NotetypeDict",
        model_base_name: str,
        ntss: List["NotetypeSetting"],
        show_tooltip_on_exception=True,
    ) -> bool:
        # Takes a model and a list of note type setting objects (ntss) and updates the model so that
        # the passed settings in the model are set to the values of these settings in self.conf
        # If this function is successful it will return True,
        # if there is an exception while parsing the notetype it will return False (and show a tooltip)
        parse_exception = None
        for nts in ntss:
            try:
                model.update(
                    nts.updated_model(
                        model=model,
                        model_base_name=model_base_name,
                        conf=self.conf,
                    )
                )
            except NotetypeSettingException as e:
                parse_exception = e

        if parse_exception:
            message = f"failed parsing {model['name']}:\n{str(parse_exception)}"
            if show_tooltip_on_exception:
                tooltip(message)
            print(message)
            return False

        return True

    def _apply_setting_changes_for_all_notetypes(self):
        for notetype_name in anking_notetype_names():
            for model in self._notetype_versions(notetype_name):
                if not model:
                    continue
                ntss = ntss_for_model(model)
                self._safe_update_model_settings(
                    model=model, model_base_name=notetype_name, ntss=ntss
                )
                mw.col.models.update_dict(model)

    def _notetype_versions(self, notetype_name: str) -> List["NotetypeDict"]:
        """
        This is done to make this add-on compatible with note types created by AnkiHub decks.
        Returns a list of all notetype versions of the notetype in the collection.
        """
        models = [
            mw.col.models.get(x.id)  # type: ignore
            for x in mw.col.models.all_names_and_ids()
            if x.name == notetype_name
            or re.match(ANKIHUB_NOTETYPE_RE.format(notetype_name=notetype_name), x.name)
            or re.match(NOTETYPE_COPY_RE.format(notetype_name=notetype_name), x.name)
        ]
        return models

    def _all_supported_note_types(self) -> List[str]:
        return [
            version["name"]
            for base_name in anking_notetype_names()
            for version in self._notetype_versions(base_name)
        ]

    def _base_name(self, notetype_name: str) -> str:
        return next(
            (
                name
                for name in anking_notetype_names()
                if notetype_name.startswith(name + " ")
            ),
            None,
        )

    # clayout
    def _update_clayout_model(self, model):
        # update templates
        # keep scrollbar in note type manager window where it was
        # add basic mark to the change tracker
        scroll_bar = self.clayout.tform.edit_area.verticalScrollBar()
        scroll_pos = scroll_bar.value()
        self.clayout.model = model
        self.clayout.templates = model["tmpls"]
        self.clayout.change_tracker.mark_basic()
        self.clayout.update_current_ordinal_and_redraw(self.clayout.ord)
        scroll_bar.setValue(min(scroll_pos, scroll_bar.maximum()))
