package com.marcusrosado.diablo.render

data class Span(
    val text: String,
    val bold: Boolean = false,
    val italic: Boolean = false,
    val code: Boolean = false,
    val strike: Boolean = false,
    val link: String? = null,
    val html: Boolean = false,
)

enum class Align { None, Left, Center, Right }

sealed interface Block {
    data class Heading(val level: Int, val spans: List<Span>, val quote: Int = 0) : Block
    data class Paragraph(val spans: List<Span>, val quote: Int = 0) : Block
    data class Item(
        val ordered: Boolean,
        val number: Long,
        val depth: Int,
        val checked: Boolean?,
        val spans: List<Span>,
        val quote: Int = 0,
    ) : Block
    data class Code(val lang: String?, val text: String, val quote: Int = 0) : Block
    data class Table(
        val align: List<Align>,
        val header: List<List<Span>>,
        val rows: List<List<List<Span>>>,
    ) : Block
    data class Html(val html: String) : Block
    data class Svg(val svg: String, val animated: Boolean) : Block
    data class Image(val url: String, val alt: String) : Block
    data object Rule : Block
}
