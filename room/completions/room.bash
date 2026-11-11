# bash completion for room

_room() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    local cmds="status why pause resume try scenes lamps log setup check daemon --version --help"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=($(compgen -W "$cmds" -- "$cur"))
        return
    fi
    case ${COMP_WORDS[1]} in
        status) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        pause) (( COMP_CWORD == 2 )) && COMPREPLY=($(compgen -W "$(room names durations 2>/dev/null)" -- "$cur")) ;;
        try)
            if (( COMP_CWORD == 2 )); then
                COMPREPLY=($(compgen -W "$(room names scenes 2>/dev/null)" -- "$cur"))
            elif (( COMP_CWORD == 3 )); then
                COMPREPLY=($(compgen -W "5 8 15 30" -- "$cur"))
            fi ;;
        log) (( COMP_CWORD == 2 )) && COMPREPLY=($(compgen -W "10 20 50 100" -- "$cur")) ;;
    esac
}

complete -F _room room
