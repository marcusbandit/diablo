# bash completion for remind
_remind() {
  local cur prev cmd
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  cmd=""
  for word in "${COMP_WORDS[@]:1:COMP_CWORD-1}"; do
    case "$word" in add|list|cancel|run) cmd="$word"; break ;; esac
  done
  case "$prev" in
    --db) COMPREPLY=($(compgen -f -- "$cur")); return ;;
    -r|--repeat) COMPREPLY=($(compgen -W "none daily weekly weekdays" -- "$cur")); return ;;
  esac
  case "$cmd" in
    "") COMPREPLY=($(compgen -W "add list cancel run --db --json --help" -- "$cur")) ;;
    add) COMPREPLY=($(compgen -W "--repeat --task +10m +20m +30m +1h +2h +1d" -- "$cur")) ;;
    list) COMPREPLY=($(compgen -W "--all" -- "$cur")) ;;
    cancel) COMPREPLY=($(compgen -W "$(remind list 2>/dev/null | sed -n 's/^#\([0-9]*\) .*/\1/p')" -- "$cur")) ;;
  esac
}
complete -F _remind remind
