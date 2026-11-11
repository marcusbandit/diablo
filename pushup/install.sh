#!/usr/bin/env bash
# Link pushup into ~/.local/bin, install completions, and start the user service.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/.local/bin ~/.local/share/zsh/site-functions ~/.local/share/bash-completion/completions ~/.config/systemd/user
ln -sf "$here/pushup" ~/.local/bin/pushup
ln -sf "$here/completions/_pushup" ~/.local/share/zsh/site-functions/_pushup
ln -sf "$here/completions/pushup.bash" ~/.local/share/bash-completion/completions/pushup
ln -sf "$here/systemd/pushup.service" ~/.config/systemd/user/pushup.service
# The banditshell launcher plugin: `pu` in the launcher. Harmless where banditshell isn't installed.
mkdir -p ~/.config/banditshell/plugins
ln -sfn "$here/banditshell-plugin" ~/.config/banditshell/plugins/pushup
systemctl --user daemon-reload
systemctl --user enable --now pushup.service
echo "pushup installed. 'pushup status' shows the next set."
