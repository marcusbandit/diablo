package com.marcusrosado.diablo.listener

/**
 * A reply as a notification can show it: plain lines. Tables, code and pictures do not fit,
 * so each becomes a short summary.
 */
object Plain {
  private val fence = Regex("^\\s*(```|~~~)\\s*(\\S*)")
  private val tableRule = Regex("^\\s*\\|?\\s*:?-{2,}")
  private val image = Regex("!\\[([^\\]]*)]\\([^)]*\\)")
  private val link = Regex("\\[([^\\]]+)]\\([^)]*\\)")
  private val tag = Regex("<[^>]+>")
  private val marks = Regex("(\\*\\*|__|~~|`)")
  private val emphasis = Regex("(?<![\\w*])[*_]([^*_\\n]+)[*_](?![\\w*])")
  private val heading = Regex("^\\s{0,3}#{1,6}\\s+")
  private val quote = Regex("^\\s*>\\s?")
  private val task = Regex("^(\\s*)[-*+]\\s+\\[([ xX])]\\s+")
  private val bullet = Regex("^(\\s*)[-*+]\\s+")
  private val space = Regex("[ \\t]+")

  fun of(markdown: String): String {
    val out = mutableListOf<String>()
    val lines = markdown.replace("\r", "").lines()
    var i = 0
    while (i < lines.size) {
      val line = lines[i]
      val open = fence.find(line)
      if (open != null) {
        val lang = open.groupValues[2]
        var n = 0
        i++
        while (i < lines.size && !lines[i].trimStart().startsWith(open.groupValues[1])) { n++; i++ }
        i++
        out += "[${if (lang.isNotBlank()) "$lang " else ""}code, $n ${if (n == 1) "line" else "lines"}]"
        continue
      }
      if (line.trimStart().startsWith("|") && i + 1 < lines.size && tableRule.containsMatchIn(lines[i + 1])) {
        val head = line.trim().trim('|').split('|').joinToString(", ") { inline(it) }
        var rows = 0
        i += 2
        while (i < lines.size && lines[i].trimStart().startsWith("|")) { rows++; i++ }
        out += "[table, $rows ${if (rows == 1) "row" else "rows"}: $head]"
        continue
      }
      var text = line
      text = task.replace(text) { m -> m.groupValues[1] + if (m.groupValues[2].isBlank()) "[ ] " else "[x] " }
      text = bullet.replace(text) { m -> m.groupValues[1] + "• " }
      text = heading.replace(text, "")
      text = quote.replace(text, "")
      val plain = inline(text)
      if (plain.isNotBlank() && !plain.matches(Regex("^[-*_]{3,}$"))) out += plain
      i++
    }
    return out.joinToString("\n").trim()
  }

  private fun inline(text: String): String {
    var t = image.replace(text) { m -> if (m.groupValues[1].isBlank()) "[picture]" else "[picture: ${m.groupValues[1]}]" }
    t = link.replace(t) { m -> m.groupValues[1] }
    t = t.replace(Regex("<br\\s*/?>", RegexOption.IGNORE_CASE), " ")
    t = tag.replace(t, "")
    t = marks.replace(t, "")
    t = emphasis.replace(t) { m -> m.groupValues[1] }
    return space.replace(t, " ").trim()
  }
}
