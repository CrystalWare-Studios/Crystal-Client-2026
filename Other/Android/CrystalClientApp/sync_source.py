import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS_SRC = os.path.join(
    HERE, "..", "..", "..", "PC", "Windows",
    "Crystal-Chatbox-Source-Code", "Crystal Chatbox",
)

SKIP_FOR_ANDROID = set()

PY_FILES_EXCLUDE = {
    "main.py",
}


def sync():
    dest = HERE
    copied = []

    for name in sorted(os.listdir(WINDOWS_SRC)):
        if name in PY_FILES_EXCLUDE:
            continue
        if not name.endswith(".py"):
            continue
        src_path = os.path.join(WINDOWS_SRC, name)
        if not os.path.isfile(src_path):
            continue
        shutil.copy2(src_path, os.path.join(dest, name))
        copied.append(name)

    for folder in ("templates", "static"):
        src_folder = os.path.join(WINDOWS_SRC, folder)
        dest_folder = os.path.join(dest, folder)
        if os.path.isdir(dest_folder):
            shutil.rmtree(dest_folder)
        shutil.copytree(src_folder, dest_folder)
        copied.append(folder + "/")

    print(f"Synced {len(copied)} items from Windows source into {dest}:")
    for item in copied:
        print(f"  {item}")


if __name__ == "__main__":
    sync()
