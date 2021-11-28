import re
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Callable, Dict, List

from anki.models import ModelManager, NotetypeDict
from aqt import mw
from aqt.clayout import CardLayout
from aqt.utils import askUser, showInfo, tooltip
from PyQt5.QtCore import *  # type: ignore
from PyQt5.QtGui import *  # type: ignore
from PyQt5.QtWidgets import *

from .ankiaddonconfig import ConfigManager, ConfigWindow
from .ankiaddonconfig.window import ConfigLayout
from .model_settings import (
    anking_notetype_templates,
    btn_name_to_shortcut_odict,
    general_settings,
    general_settings_defaults,
    setting_configs,
    settings_by_notetype,
)


class NoteTypeSetting(ABC):
    def __init__(self, config: Dict):
        self.config = config

    @abstractmethod
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        pass

    def add_widget_to_general_config_layout(self, layout: ConfigLayout):
        self.add_widget_to_config_layout(layout, "general")

    def register_general_setting(self, conf: ConfigManager):
        def update_all(key, value):
            if self.key("general") != key:
                return
            for notetype_name in settings_by_notetype.keys():
                if (
                    not self.config["setting_name"]
                    in settings_by_notetype[notetype_name]
                ):
                    continue
                conf.set(self.key(notetype_name), value, trigger_change_hook=False)
            conf.config_window.update_widgets()

        conf.on_change(update_all)

    @staticmethod
    def from_config(config: Dict) -> "NoteTypeSetting":
        if config["type"] == "checkbox":
            return CheckboxSetting(config)
        if config["type"] == "re_checkbox":
            return ReCheckboxSetting(config)
        if config["type"] == "text":
            return LineEditSetting(config)
        if config["type"] == "number":
            return NumberEditSetting(config)
        if config["type"] == "shortcut":
            return ShortcutSetting(config)
        if config["type"] == "dropdown":
            return DropdownSetting(config)
        if config["type"] == "color":
            return ColorSetting(config)
        if config["type"] == "font_family":
            return FontFamilySetting(config)
        else:
            raise Exception(
                f"unkown NoteTypeSetting type: {config.get('type', 'None')}"
            )

    def setting_value(self, model: NotetypeDict) -> Any:
        section = self._relevant_template_section(model)
        result = self._extract_setting_value(section)
        return result

    def _relevant_template_section(self, model: NotetypeDict):
        template_text = self._relevant_template_text(model)
        section_match = re.search(self.config["regex"], template_text)
        if not section_match:
            raise NotetypeParseException(
                f"could not find '{self.config['name']}' in {self.config['file']} template of notetype '{model['name']}'"
            )
        result = section_match.group(0)
        return result

    @abstractmethod
    def _extract_setting_value(self, section: str) -> Any:
        pass

    def updated_model(
        self, model: NotetypeDict, notetype_name: str, conf: ConfigManager
    ) -> NotetypeDict:
        result = model.copy()
        section = self._relevant_template_section(result)
        setting_value = conf[self.key(notetype_name)]
        processed_section = self._set_setting_value(section, setting_value)
        updated_text = self._relevant_template_text(result).replace(
            section, processed_section, 1
        )

        templates = result["tmpls"]
        assert len(templates) == 1
        template = templates[0]

        if self.config["file"] == "front":
            template["qfmt"] = updated_text
        elif self.config["file"] == "back":
            template["afmt"] = updated_text
        else:
            result["css"] = updated_text

        return result

    @abstractmethod
    def _set_setting_value(self, section: str, setting_value: Any):
        pass

    def key(self, notetype_name: str) -> str:
        return f'{notetype_name}.{self.config["setting_name"]}'

    def _relevant_template_text(self, model: NotetypeDict) -> str:
        templates = model["tmpls"]
        assert len(templates) == 1
        template = templates[0]

        if self.config["file"] == "front":
            result = template["qfmt"]
        elif self.config["file"] == "back":
            result = template["afmt"]
        else:
            result = model["css"]
        return result


class NotetypeParseException(Exception):
    pass


class ReCheckboxSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.checkbox(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        replacement_pairs = self.config["replacement_pairs"]
        checked = all(y in section for _, y in replacement_pairs)
        unchecked = all(x in section for x, _ in replacement_pairs)
        if not ((checked or unchecked) and not (checked and unchecked)):
            raise NotetypeParseException(
                f"error involving {replacement_pairs=} and {section=}"
            )
        return checked

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        result = section
        replacement_pairs = self.config["replacement_pairs"]
        for x, y in replacement_pairs:
            if setting_value:
                result = result.replace(x, y)
            else:
                result = result.replace(y, x)

        return result


class CheckboxSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.checkbox(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        value = re.search(self.config["regex"], section).group(1)
        assert value in ["true", "false"]
        return value == "true"

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        current_value = self._extract_setting_value(section)
        current_value_str = "true" if current_value else "false"
        new_value_str = "true" if setting_value else "false"
        result = section.replace(current_value_str, new_value_str, 1)
        return result


class LineEditSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.text_input(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        return re.search(self.config["regex"], section).group(1)

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        m = re.search(self.config["regex"], section)
        start, end = m.span(1)
        result = section[:start] + setting_value + section[end:]
        return result


class FontFamilySetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.font_family_combobox(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        return re.search(self.config["regex"], section).group(1)

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        m = re.search(self.config["regex"], section)
        start, end = m.span(1)
        result = section[:start] + setting_value + section[end:]
        return result


class DropdownSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.dropdown(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
            labels=self.config["options"],
            values=self.config["options"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        return re.search(self.config["regex"], section).group(1)

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        current_value = self._extract_setting_value(section)
        result = section.replace(current_value, setting_value, 1)
        return result


class ColorSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.color_input(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        color_str = re.search(self.config["regex"], section).group(1)
        if (
            self.config.get("with_inherit_option", False)
            and str(color_str) != "transparent"
        ):
            return "inherit"
        return color_str

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        current_value = self._extract_setting_value(section)
        if (
            self.config.get("with_inherit_option", False)
            and setting_value != "transparent"
        ):
            result = section.replace(current_value, "inherit", 1)
        else:
            result = section.replace(current_value, setting_value, 1)
        return result


class ShortcutSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.shortcut_edit(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
        )

    def _extract_setting_value(self, section: str) -> Any:
        shortcut_str = re.search(self.config["regex"], section).group(1)
        return shortcut_str

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        m = re.search(self.config["regex"], section)
        start, end = m.span(1)
        result = section[:start] + setting_value + section[end:]
        return result


class NumberEditSetting(NoteTypeSetting):
    def add_widget_to_config_layout(self, layout: ConfigLayout, notetype_name: str):
        layout.number_input(
            key=self.key(notetype_name),
            description=self.config["name"],
            tooltip=self.config["tooltip"],
            minimum=self.config.get("min", None),
            maximum=self.config.get("max", 99999),
        )

    def _extract_setting_value(self, section: str) -> Any:
        value_str = re.search(self.config["regex"], section).group(1)
        return int(value_str)

    def _set_setting_value(self, section: str, setting_value: Any) -> str:
        current_value = self._extract_setting_value(section)
        result = section.replace(str(current_value), str(setting_value), 1)
        return result


def notetype_settings_tab(notetype_name: str, ntss: List[NoteTypeSetting]) -> Callable:
    def tab(window: ConfigWindow):
        tab = window.add_tab(notetype_name)

        notetype_names = [nt.name for nt in mw.col.models.all_names_and_ids()]
        if notetype_name in notetype_names:
            scroll = tab.scroll_layout()
            add_nts_widgets_to_layout(scroll, ntss, notetype_name)
            scroll.stretch()
        else:
            tab.text("the notetype is not in the collection")
            tab.stretch()

        tab.button(
            "Reset",
            on_click=lambda: reset_notetype_and_reload_ui(notetype_name, window),
        )

    return tab


def reset_notetype_and_reload_ui(notetype_name, window: ConfigWindow):
    if askUser(
        f"Do you really want to reset the <b>{notetype_name}</b> notetype to its default form?",
        defaultno=True,
    ):
        mm: ModelManager = mw.col.models
        model = mm.by_name(notetype_name)
        front, back, styling = anking_notetype_templates()[notetype_name]
        model["tmpls"][0]["qfmt"] = front
        model["tmpls"][0]["afmt"] = back
        model["css"] = styling
        mm.update_dict(model)

        read_in_settings_from_notetypes(window.conf)
        window.update_widgets()

        tooltip("Notetype was reset", parent=window, period=1200)


def general_tab(ntss: List[NoteTypeSetting]) -> Callable:
    def tab(window: ConfigWindow):
        tab = window.add_tab("General")

        scroll = tab.scroll_layout()
        add_nts_widgets_to_layout(scroll, ntss, None, general=True)
        scroll.stretch()

        for nts in ntss:
            nts.register_general_setting(tab.conf)

        tab.space(10)
        tab.text(
            "Changes made here will be applied to all notetypes that have this setting",
            bold=True,
            multiline=True,
        )

    return tab


def add_nts_widgets_to_layout(
    layout: ConfigLayout, ntss: List[NoteTypeSetting], notetype_name: str, general=False
) -> None:

    if general:
        assert notetype_name == None

    nts_to_section = {
        nts: section_name
        for nts in ntss
        if (section_name := nts.config.get("section", None))
    }

    section_to_ntss: Dict[str, List[NoteTypeSetting]] = defaultdict(lambda: [])
    for nts, section in nts_to_section.items():
        section_to_ntss[section].append(nts)

    for section_name, section_ntss in section_to_ntss.items():
        section = layout.collapsible_section(section_name)
        for nts in section_ntss:
            if general:
                nts.add_widget_to_general_config_layout(section)
            else:
                nts.add_widget_to_config_layout(section, notetype_name)
            section.space(7)
        layout.hseparator()
        layout.space(10)

    other_ntss: List[NoteTypeSetting] = [
        nts for nts in ntss if nts not in nts_to_section.keys()
    ]
    for nts in other_ntss:
        if general:
            nts.add_widget_to_general_config_layout(layout)
        else:
            nts.add_widget_to_config_layout(layout, notetype_name)
        layout.space(7)


def change_window_settings(window: ConfigWindow, on_save, clayout=None):
    window.setWindowTitle("AnKing note types")
    window.setMinimumHeight(500)
    window.setMinimumWidth(500)

    # hide reset and advanced buttons
    window.reset_btn.hide()
    window.advanced_btn.hide()

    # overwrite on_save function
    window.save_btn.clicked.disconnect()  # type: ignore
    window.save_btn.clicked.connect(lambda: on_save(window))  # type: ignore

    window.execute_on_save(on_save)

    def update_clayout_on_reset():
        model = clayout.model
        notetype_name = model["name"]
        for nts in ntss_for_notetype(notetype_name):
            # XXX NotetypeParseException could occur
            model = nts.updated_model(model, notetype_name, window.conf)

        clayout.model = model
        clayout.change_tracker.mark_basic()
        clayout.update_current_ordinal_and_redraw(clayout.ord)

    if clayout:
        window.reset_btn.clicked.connect(update_clayout_on_reset)  # type: ignore
        change_tab_to_current_notetype(window, clayout)


def change_tab_to_current_notetype(
    window: ConfigWindow,
    clayout: CardLayout,
) -> None:
    notetype_name = clayout.model["name"]
    tab_widget = window.main_tab

    def get_tab_by_name(tab_name):
        return next(
            (
                index
                for index in range(tab_widget.count())
                if tab_name == tab_widget.tabText(index)
            ),
            None,
        )

    tab_widget.setCurrentIndex(get_tab_by_name(notetype_name))


def ntss_for_notetype(notetype_name) -> List[NoteTypeSetting]:
    result = []
    for name in settings_by_notetype.get(notetype_name, []):
        setting_config = setting_configs[name]
        setting_config["setting_name"] = name
        result.append(NoteTypeSetting.from_config(setting_config))

    result = adjust_hint_button_nts_order(result, notetype_name)
    return result


def adjust_hint_button_nts_order(
    ntss: List[NoteTypeSetting], notetype_name: str
) -> List[NoteTypeSetting]:
    # adjusts the order of the hint button settings to be the same as
    # on the card of the notetype

    hint_button_ntss = [
        nts for nts in ntss if nts.config.get("hint_button_setting", False)
    ]
    ordered_btn_names = list(btn_name_to_shortcut_odict(notetype_name).keys())
    ordered_hint_button_ntss = sorted(
        hint_button_ntss,
        key=lambda nts: ordered_btn_names.index(nts.config["hint_button_setting"]),
    )

    other_ntss = [nts for nts in ntss if nts not in hint_button_ntss]
    return ordered_hint_button_ntss + other_ntss


def general_ntss() -> List[NoteTypeSetting]:
    result = []
    for name in general_settings:
        setting_config = setting_configs[name]
        setting_config["setting_name"] = name
        result.append(NoteTypeSetting.from_config(setting_config))
    return result


def safe_update_model(ntss: List[NoteTypeSetting], model, conf: ConfigManager):
    result = model.copy()
    parse_exception = None  # show only one error if any
    for nts in ntss:
        try:
            result = nts.updated_model(result, result["name"], conf)
        except NotetypeParseException as e:
            parse_exception = e
    if parse_exception:
        tooltip(f"failed parsing notetype:\n{str(parse_exception)}")

    return result


def read_in_settings_from_notetypes(conf: ConfigManager):
    error_msg = ""
    for notetype_name in settings_by_notetype.keys():
        model = mw.col.models.by_name(notetype_name)
        if not model:
            continue
        for nts in ntss_for_notetype(notetype_name):
            try:
                conf[nts.key(notetype_name)] = nts.setting_value(model)
            except NotetypeParseException as e:
                error_msg += f"failed parsing notetype:\n{str(e)}\n\n"

    if error_msg:
        showInfo(error_msg)


def read_in_general_settings(conf: ConfigManager):
    for key, value in general_settings_defaults().items():
        conf[f"general.{key}"] = value


def update_notetypes(conf: ConfigManager):
    for notetype_name in settings_by_notetype.keys():
        model = mw.col.models.by_name(notetype_name)
        if not model:
            continue
        ntss = ntss_for_notetype(notetype_name)
        model = safe_update_model(ntss, model, conf)
        mw.col.models.update_dict(model)


def on_save(window: ConfigWindow):
    update_notetypes(window.conf)
    window.close()


def open_config_window(clayout: CardLayout = None):

    # ankiaddonconfig's ConfigManager is used here in a way that is not intended
    # the save functionality gets overwritten and nothing gets saved to the Anki
    # addon config
    # the config is populated at the start with the current setting values parsed
    # from the notetype and then used to update the settings
    conf = ConfigManager()

    # read in settings from notetypes and general ones into config
    read_in_settings_from_notetypes(conf)
    read_in_general_settings(conf)

    # if in live preview mode read in current not confirmed settings
    if clayout:
        model = clayout.model
        notetype_name = clayout.model["name"]
        for nts in ntss_for_notetype(notetype_name):
            conf[nts.key(notetype_name)] = nts.setting_value(model)

    # add general tab
    conf.add_config_tab(general_tab(general_ntss()))

    # setup tabs for all notetypes
    for notetype_name in sorted(settings_by_notetype.keys()):
        conf.add_config_tab(
            notetype_settings_tab(notetype_name, ntss_for_notetype(notetype_name))
        )

    # setup live update of clayout model on changes
    def update_clayout_model(key: str, _: Any):
        model = clayout.model
        notetype_name, setting_name = key.split(".")
        if notetype_name != clayout.model["name"]:
            return

        nts = NoteTypeSetting.from_config(setting_configs[setting_name])
        model = safe_update_model([nts], model, conf)

        clayout.model = model
        clayout.change_tracker.mark_basic()
        clayout.update_current_ordinal_and_redraw(clayout.ord)

    if clayout:
        conf.on_change(update_clayout_model)

    # change window settings, overwrite on_save, setup notetype updates
    conf.on_window_open(
        lambda window: change_window_settings(window, on_save=on_save, clayout=clayout)
    )

    # open the config window
    conf.open_config()
