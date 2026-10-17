package app.messagegateway.sms.receiver

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SmsResultsTest {
    @Test fun `known send errors are described`() {
        assertEquals("No service (code 4)", SmsResults.describeSendError(4))
        assertTrue(SmsResults.describeSendError(2).contains("airplane"))
    }

    @Test fun `unknown send errors still say the code`() = assertEquals("Send failed (code 99)", SmsResults.describeSendError(99))

    @Test fun `delivery status zero means delivered`() = assertEquals(true, SmsResults.deliveryOutcome(0, false))

    @Test fun `permanent failure status means failed`() = assertEquals(false, SmsResults.deliveryOutcome(0x40, true))

    @Test fun `temporary status means still trying`() = assertNull(SmsResults.deliveryOutcome(0x20, true))

    @Test fun `no status falls back to the result code`() {
        assertTrue(SmsResults.deliveryOutcome(null, true)!!)
        assertFalse(SmsResults.deliveryOutcome(null, false)!!)
    }
}
