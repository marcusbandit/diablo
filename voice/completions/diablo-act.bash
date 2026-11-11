# bash completion for diablo-act
_diablo_act() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  local repo
  repo="$(dirname "$(readlink -f "$(command -v diablo-act)")")/.."
  case "$prev" in
    --look) COMPREPLY=($(compgen -W "$(ls "$repo/assets/faces/eyes" 2>/dev/null)" -- "$cur")); return ;;
    --size) COMPREPLY=($(compgen -W "720 1080 1440 2160" -- "$cur")); return ;;
    --lang) COMPREPLY=($(compgen -W "auto en da es ja" -- "$cur")); return ;;
    -o|--out) COMPREPLY=($(compgen -f -- "$cur")); return ;;
    --text|--colour|--background) return ;;
  esac
  if [[ "$cur" == -* ]]; then
    COMPREPLY=($(compgen -W "--last --record --text --lang --en --us --da --dk --es --ja --jp --look -o --out --size --colour --background --transparent --no-open -h --help" -- "$cur"))
  else
    COMPREPLY=($(compgen -f -- "$cur"))
  fi
}
complete -F _diablo_act diablo-act
