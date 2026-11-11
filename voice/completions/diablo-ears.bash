_diablo_ears() {
  local cur=${COMP_WORDS[COMP_CWORD]}
  if (( COMP_CWORD == 1 )); then
    COMPREPLY=($(compgen -W "train retrain status chimes timings recordings recheck run" -- "$cur"))
  elif [[ ${COMP_WORDS[1]} == train ]]; then
    COMPREPLY=($(compgen -W "--fresh" -- "$cur"))
  elif [[ ${COMP_WORDS[1]} == recheck ]]; then
    COMPREPLY=($(compgen -W "last $(ls ~/.local/share/diablo/turns/ 2>/dev/null | sed -n 's/\.wav$//p' | tail -20)" -- "$cur"))
  elif [[ ${COMP_WORDS[1]} == timings ]]; then
    if [[ ${COMP_WORDS[COMP_CWORD-1]} == --html ]]; then
      COMPREPLY=($(compgen -f -- "$cur"))
    else
      COMPREPLY=($(compgen -W "--last --html" -- "$cur"))
    fi
  fi
}
complete -F _diablo_ears diablo-ears
