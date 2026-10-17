package app.messagegateway.sms.core

import java.security.SecureRandom
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** ISO-8601 timestamps with an explicit "+00:00" offset, the form the gateway and its clients parse. */
object Iso {
    private val formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSxxx").withZone(ZoneOffset.UTC)

    fun format(millis: Long): String = formatter.format(Instant.ofEpochMilli(millis))

    /** Null when [value] is not a valid ISO-8601 date-time. */
    fun parse(value: String): Long? = try {
        OffsetDateTime.parse(value).toInstant().toEpochMilli()
    } catch (_: Exception) {
        null
    }
}

object Ids {
    private val random = SecureRandom()
    private const val ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    fun random(length: Int): String = buildString(length) {
        repeat(length) { append(ALPHABET[random.nextInt(ALPHABET.length)]) }
    }

    fun messageId(): String = random(21)
}

object PhoneNumbers {
    private val separators = Regex("[\\s\\-().]")
    private val valid = Regex("^\\+?[0-9]{3,15}$")

    /**
     * Cleans a number for sending. Spaces and punctuation are removed. A number that already
     * starts with "+" or is short (a short code) is kept as is; others are offered to [toE164]
     * (region-aware formatting) and kept as typed when that has no answer.
     * @throws IllegalArgumentException when the number cannot be a phone number.
     */
    fun normalize(input: String, skipValidation: Boolean = false, toE164: (String) -> String? = { null }): String {
        val cleaned = input.trim().replace(separators, "")
        if (skipValidation) {
            require(cleaned.isNotEmpty()) { "Phone number is empty" }
            return cleaned
        }
        require(valid.matches(cleaned)) { "Invalid phone number: $input" }
        if (cleaned.startsWith("+") || cleaned.length <= 8) return cleaned
        return toE164(cleaned) ?: cleaned
    }
}
