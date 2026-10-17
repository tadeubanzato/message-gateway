package app.messagegateway.sms.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class UtilTest {
    @Test fun `iso format uses an explicit offset`() {
        assertEquals("2026-10-16T21:04:05.123+00:00", Iso.format(1_792_184_645_123L))
    }

    @Test fun `iso parse accepts Z and offsets`() {
        val z = Iso.parse("2026-10-16T21:04:05.123Z")
        assertEquals(1_792_184_645_123L, z)
        assertEquals(z, Iso.parse("2026-10-16T23:04:05.123+02:00"))
    }

    @Test fun `iso parse rejects junk`() {
        assertNull(Iso.parse("yesterday"))
        assertNull(Iso.parse(""))
    }

    @Test fun `phone numbers lose punctuation`() {
        assertEquals("+15551234567", PhoneNumbers.normalize("+1 (555) 123-4567"))
    }

    @Test fun `short codes are kept`() = assertEquals("12345", PhoneNumbers.normalize("12345"))

    @Test fun `local numbers use the regional formatter when it has an answer`() {
        assertEquals("+5511999990000", PhoneNumbers.normalize("11999990000") { "+55$it" })
        assertEquals("11999990000", PhoneNumbers.normalize("11999990000") { null })
    }

    @Test fun `invalid numbers are rejected`() {
        assertThrows(IllegalArgumentException::class.java) { PhoneNumbers.normalize("abc") }
        assertThrows(IllegalArgumentException::class.java) { PhoneNumbers.normalize("") }
        assertThrows(IllegalArgumentException::class.java) { PhoneNumbers.normalize("+1234567890123456") }
    }

    @Test fun `validation can be skipped`() {
        assertEquals("abc", PhoneNumbers.normalize("abc", skipValidation = true))
    }

    @Test fun `random ids have the asked length and differ`() {
        assertEquals(21, Ids.messageId().length)
        assert(Ids.messageId() != Ids.messageId())
    }
}
