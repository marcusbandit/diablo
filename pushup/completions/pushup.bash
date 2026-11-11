_pushup() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=($(compgen -W "now status challenge stretches sync stats graph log train hurt add exercises gear pause resume level spice intro check daemon" -- "$cur"))
        return
    fi
    local IFS=$'\n'
    case ${COMP_WORDS[1]}:$COMP_CWORD in
        status:2) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        challenge:2) COMPREPLY=($(compgen -W $'start\noff\nfeet-up\nrows' -- "$cur")) ;;
        challenge:3) [[ ${COMP_WORDS[2]} == feet-up || ${COMP_WORDS[2]} == rows ]] && COMPREPLY=($(compgen -W $'on\noff' -- "$cur"))
                     [[ ${COMP_WORDS[2]} == start ]] && COMPREPLY=($(compgen -W "--over" -- "$cur")) ;;
        hurt:2) COMPREPLY=($(compgen -W "off left right arms back legs core $(pushup names muscles 2>/dev/null | tr ' ' '_')" -- "$cur")) ;;
        hurt:*) COMPREPLY=($(compgen -W "left right arms back legs core $(pushup names muscles 2>/dev/null | tr ' ' '_')" -- "$cur")) ;;
        pause:2) COMPREPLY=($(compgen -W $'15m\n30m\n1h\n2h\n3h\ntomorrow' -- "$cur")) ;;
        level:2) COMPREPLY=($(compgen -W $'easy\nnormal\nhard' -- "$cur")) ;;
        stats:2) COMPREPLY=($(compgen -W $'7\n14\n30' -- "$cur")) ;;
        spice:2) COMPREPLY=($(compgen -W $'mild\nspicy' -- "$cur")) ;;
        stretches:2) COMPREPLY=($(compgen -W $'on\noff' -- "$cur")) ;;
        graph:*) [[ ${COMP_WORDS[COMP_CWORD-1]} == --tab ]] && COMPREPLY=($(compgen -W $'overview\nbody\nmoves\ntimeline' -- "$cur")) && return
                 [[ $cur == -* ]] && COMPREPLY=($(compgen -W "--tab" -- "$cur")) && return
                 (( COMP_CWORD == 2 )) && COMPREPLY=($(compgen -W $'14\n30\n60' -- "$cur")) ;;
        log:2) COMPREPLY=($(compgen -W $'5\n10\n15\n20\n3x8\n3x10\n3x12\n4x10' -- "$cur")) ;;
        log:*) [[ $cur == -* ]] && COMPREPLY=($(compgen -W "--sets" -- "$cur")) && return
               COMPREPLY=($(compgen -W "$(pushup names exercises 2>/dev/null)" -- "$cur")) ;;
        exercises:[3-9]) COMPREPLY=($(compgen -W "$(pushup names exercises 2>/dev/null)" -- "$cur")) ;;
        train:2) COMPREPLY=($(compgen -W "$(pushup names activities 2>/dev/null)" -- "$cur")) ;;
        train:3) COMPREPLY=($(compgen -W $'30m\n45m\n1h\n90m\n2h\n3h' -- "$cur")) ;;
        train:4) COMPREPLY=($(compgen -W $'easy\nnormal\nhard' -- "$cur")) ;;
        train:*) COMPREPLY=($(compgen -W "--yesterday" -- "$cur")) ;;
        exercises:2) COMPREPLY=($(compgen -W $'on\noff' -- "$cur")) ;;
        gear:2) COMPREPLY=($(compgen -W $'on\noff' -- "$cur")) ;;
        gear:[3-9]) COMPREPLY=($(compgen -W $'bar\ntable\nchairs\nparallettes' -- "$cur")) ;;
    esac
}
complete -F _pushup pushup
