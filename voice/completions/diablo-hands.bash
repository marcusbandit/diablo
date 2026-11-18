# bash completion for diablo-hands
_diablo_hands() {
  local cur prev cmd
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  cmd="${COMP_WORDS[1]}"
  if [[ $COMP_CWORD -eq 1 ]]; then
    COMPREPLY=($(compgen -W "front windows ui find-text act -h --help" -- "$cur"))
    return
  fi
  case "$prev" in
    --window)
      local wins
      wins="front $(hyprctl -j clients 2>/dev/null | python3 -c 'import json,sys
for c in json.load(sys.stdin):
    if c.get("mapped") and c.get("class"): print(c["class"])' 2>/dev/null)"
      COMPREPLY=($(compgen -W "$wins" -- "$cur"))
      return ;;
    --max) COMPREPLY=($(compgen -W "50 100 300 1000" -- "$cur")); return ;;
    --steps)
      local IFS=$'\n'
      COMPREPLY=($(compgen -W "$(printf '%s\n' \
        "'[{\"do\":\"click_text\",\"text\":\"\"}]'" \
        "'[{\"do\":\"click\",\"id\":1}]'" \
        "'[{\"do\":\"click_at\",\"x\":0,\"y\":0}]'" \
        "'[{\"do\":\"fill\",\"field\":\"\",\"text\":\"\"}]'" \
        "'[{\"do\":\"type\",\"text\":\"\"}]'" \
        "'[{\"do\":\"key\",\"key\":\"Return\"}]'" \
        "'[{\"do\":\"scroll\",\"direction\":\"down\",\"amount\":5}]'" \
        "'[{\"do\":\"open\",\"target\":\"\"}]'" \
        "'[{\"do\":\"focus\"}]'" \
        "'[{\"do\":\"wait\",\"seconds\":1}]'")" -- "$cur"))
      return ;;
  esac
  case "$cmd" in
    ui) COMPREPLY=($(compgen -W "--window --max" -- "$cur")) ;;
    find-text) [[ "$cur" == -* ]] && COMPREPLY=($(compgen -W "--window --all" -- "$cur")) ;;
    act) COMPREPLY=($(compgen -W "--window --steps" -- "$cur")) ;;
  esac
}
complete -F _diablo_hands diablo-hands
