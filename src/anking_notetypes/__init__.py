from pathlib import Path

from aqt import mw
from aqt.gui_hooks import card_layout_will_show, profile_did_open
from aqt.qt import QPushButton
from aqt.utils import askUserDialog

from .compat import add_compat_aliases
from .gui.config_window import NotetypesConfigWindow
from .gui.menu import setup_menu

ADDON_DIR_NAME = str(Path(__file__).parent.name)
RESOURCES_PATH = Path(__file__).parent / "resources"


def on_profile_did_open():
    add_compat_aliases()

    setup_menu(open_window)

    copy_resources_into_media_folder()

    replace_default_addon_config_action()

    card_layout_will_show.append(add_button_to_clayout)

    maybe_show_notetypes_update_notice()


def open_window():
    window = NotetypesConfigWindow()
    window.open()


def add_button_to_clayout(clayout):
    button = QPushButton()
    button.setAutoDefault(False)
    button.setText("Configure AnKing notetypes")

    def open_window_with_clayout():
        window = NotetypesConfigWindow(clayout)
        window.open()

    button.clicked.connect(open_window_with_clayout)
    clayout.buttons.insertWidget(1, button)


def maybe_show_notetypes_update_notice():

    # can happen when restoring data from backup
    if not mw.col:
        return

    models_with_available_updates = (
        NotetypesConfigWindow.models_with_available_updates()
    )
    if not models_with_available_updates:
        return

    conf = mw.addonManager.getConfig(ADDON_DIR_NAME)
    latest_notice_version = conf.get("latest_notified_note_type_version")
    if all(
        NotetypesConfigWindow.model_version(model) == latest_notice_version
        for model in models_with_available_updates
    ):
        return

    conf["latest_notified_note_type_version"] = NotetypesConfigWindow.model_version(
        models_with_available_updates[0]
    )

    answer = askUserDialog(
        title="AnKing note types update",
        text="New versions of the AnKing note types are available! \nYou can choose to update them in the "
        "AnKing Note Types dialog. Open the dialog now?",
        buttons=reversed(["Yes", "No", "Remind me later"]),
    ).run()
    if answer == "Yes":
        mw.addonManager.writeConfig(ADDON_DIR_NAME, conf)
        open_window()
    elif answer == "No":
        mw.addonManager.writeConfig(ADDON_DIR_NAME, conf)
    elif answer == "Remind me later":
        pass


def copy_resources_into_media_folder():
    # add recources of all notetypes to collection media folder
    for file in Path(RESOURCES_PATH).iterdir():
        if not mw.col.media.have(file.name):
            mw.col.media.add_file(str(file.absolute()))


def replace_default_addon_config_action():
    mw.addonManager.setConfigAction(ADDON_DIR_NAME, open_window)


if mw is not None:
    profile_did_open.append(on_profile_did_open)
