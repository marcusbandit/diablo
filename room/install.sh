#!/usr/bin/env bash
# Link room into ~/.local/bin, install completions, the launcher plugin and the user service.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/.local/bin ~/.local/share/zsh/site-functions ~/.local/share/bash-completion/completions \
    ~/.config/systemd/user ~/.local/share/applications
ln -sf "$here/bin/room" ~/.local/bin/room
ln -sf "$here/completions/_room" ~/.local/share/zsh/site-functions/_room
ln -sf "$here/completions/room.bash" ~/.local/share/bash-completion/completions/room
ln -sf "$here/systemd/room.service" ~/.config/systemd/user/room.service
ln -sf "$here/room-setup.desktop" ~/.local/share/applications/room-setup.desktop
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
ln -sf "$here/assets/room.svg" ~/.local/share/icons/hicolor/scalable/apps/room.svg
# The banditshell launcher plugin: type `room` in the launcher. Harmless without banditshell.
mkdir -p ~/.config/banditshell/plugins
ln -sfn "$here/banditshell-plugin" ~/.config/banditshell/plugins/room
systemctl --user daemon-reload
systemctl --user enable --now room.service
echo "room installed. 'room' shows what the lights are doing; 'room setup' walks through it again."
