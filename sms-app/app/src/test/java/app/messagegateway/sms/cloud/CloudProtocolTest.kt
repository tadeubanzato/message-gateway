package app.messagegateway.sms.cloud

import app.messagegateway.sms.core.Iso
import app.messagegateway.sms.data.MessageEntity
import app.messagegateway.sms.data.MessageWithRecipients
import app.messagegateway.sms.data.RecipientEntity
import app.messagegateway.sms.data.StateEntity
import app.messagegateway.sms.domain.ProcessingState
import app.messagegateway.sms.server.ApiJson
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class CloudUrlsTest {
    @Test fun `public server`() =
        assertEquals("https://api.sms-gate.app/mobile/v1", CloudUrls.mobileBase(CloudUrls.SERVER_PUBLIC, "ignored"))

    @Test fun `bare host gets the api path`() =
        assertEquals("https://sms.example.com/api/mobile/v1", CloudUrls.normalizePrivate("https://sms.example.com/"))

    @Test fun `api path gets the version`() =
        assertEquals("https://sms.example.com/api/mobile/v1", CloudUrls.normalizePrivate("https://sms.example.com/api"))

    @Test fun `full address is kept`() =
        assertEquals("https://sms.example.com/api/mobile/v1", CloudUrls.normalizePrivate("  https://sms.example.com/api/mobile/v1/ "))

    @Test fun `a port and a path prefix survive`() =
        assertEquals("https://h.example:8443/sms/api/mobile/v1", CloudUrls.normalizePrivate("https://h.example:8443/sms/api"))

    @Test fun `address without a scheme is rejected`() {
        assertThrows(IllegalArgumentException::class.java) { CloudUrls.normalizePrivate("sms.example.com") }
        assertThrows(IllegalArgumentException::class.java) { CloudUrls.normalizePrivate("https://") }
        assertThrows(IllegalArgumentException::class.java) { CloudUrls.normalizePrivate("") }
    }

    @Test fun `third party address is derived`() =
        assertEquals("https://api.sms-gate.app/3rdparty/v1", CloudUrls.thirdPartyBase(CloudUrls.PUBLIC_MOBILE))
}

class SseParserTest {
    private fun parse(vararg lines: String): List<SseParser.Event> {
        val parser = SseParser()
        return lines.mapNotNull { parser.feed(it) }
    }

    @Test fun `a named event`() =
        assertEquals(listOf(SseParser.Event("MessageEnqueued", "{}")), parse("event: MessageEnqueued", "data: {}", ""))

    @Test fun `keepalive comments are ignored`() = assertEquals(emptyList<SseParser.Event>(), parse(":keepalive", ""))

    @Test fun `an event without a name is a message enqueued`() =
        assertEquals(listOf(SseParser.Event("MessageEnqueued", "x")), parse("data: x", ""))

    @Test fun `multi-line data is joined`() =
        assertEquals(listOf(SseParser.Event("WebhooksUpdated", "a\nb")), parse("event: WebhooksUpdated", "data: a", "data: b", ""))

    @Test fun `events do not leak into each other`() {
        val events = parse("event: A", "data: 1", "", ":keepalive", "", "event: B", "data: 2", "")
        assertEquals(listOf(SseParser.Event("A", "1"), SseParser.Event("B", "2")), events)
    }

    @Test fun `a blank line with no data produces nothing`() = assertEquals(emptyList<SseParser.Event>(), parse("event: A", ""))

    @Test fun `value without a space after the colon`() =
        assertEquals(listOf(SseParser.Event("A", "1")), parse("event:A", "data:1", ""))
}

class CloudMappingTest {
    private val now = 1_800_000_000_000L

    private fun message(block: (CloudMessage) -> CloudMessage = { it }) =
        block(CloudMessage(id = "m1", textMessage = CloudTextMessage("hi"), phoneNumbers = listOf("+15551234567")))

    private fun accepted(m: CloudMessage) = (CloudMapping.toNewMessage(m, now) as CloudMapping.Mapped.Accept).message
    private fun rejected(m: CloudMessage) = (CloudMapping.toNewMessage(m, now) as CloudMapping.Mapped.Reject).reason

    @Test fun `a plain text message is accepted as cloud sourced`() {
        val m = accepted(message())
        assertEquals("m1", m.id)
        assertEquals("hi", m.text)
        assertEquals(MessageEntity.SOURCE_CLOUD, m.source)
        assertTrue("the server already validated the numbers", m.skipPhoneValidation)
        assertTrue("delivery reports default on", m.withDeliveryReport)
    }

    @Test fun `the deprecated message field still works`() {
        assertEquals("old", accepted(CloudMessage(id = "m", message = "old", phoneNumbers = listOf("1"))).text)
    }

    @Test fun `options are carried over`() {
        val m = accepted(message { it.copy(simNumber = 2, withDeliveryReport = false, priority = 100) })
        assertEquals(2, m.simNumber)
        assertEquals(false, m.withDeliveryReport)
        assertEquals(100, m.priority)
    }

    @Test fun `unsupported content is rejected with a reason`() {
        assertTrue(rejected(message { it.copy(isEncrypted = true) }).contains("Encrypted"))
        assertTrue(rejected(message { it.copy(dataMessage = JsonPrimitive("x")) }).contains("Data"))
        assertTrue(rejected(message { it.copy(mmsMessage = JsonPrimitive("x")) }).contains("MMS"))
    }

    @Test fun `empty text and no recipients are rejected`() {
        assertTrue(rejected(CloudMessage(id = "m", phoneNumbers = listOf("1"))).contains("no text"))
        assertTrue(rejected(message { it.copy(phoneNumbers = emptyList()) }).contains("recipients"))
    }

    @Test fun `an expired message is rejected`() {
        val past = Iso.format(now - 1_000)
        assertEquals("Message expired", rejected(message { it.copy(validUntil = past) }))
    }

    @Test fun `a schedule in the past means send now, one in the future is kept`() {
        assertNull(accepted(message { it.copy(scheduleAt = Iso.format(now - 5_000)) }).scheduleAt)
        assertEquals(now + 60_000, accepted(message { it.copy(scheduleAt = Iso.format(now + 60_000)) }).scheduleAt)
    }

    @Test fun `duplicate numbers are collapsed`() {
        assertEquals(listOf("1", "2"), accepted(message { it.copy(phoneNumbers = listOf("1", "2", "1")) }).phoneNumbers)
    }

    @Test fun `a report carries the state, recipients and when each state was reached`() {
        val stored = MessageWithRecipients(
            MessageEntity("m1", "hi", 1, null, null, 0, null, true, true, ProcessingState.Sent, "Cloud"),
            listOf(RecipientEntity("m1", "+2", ProcessingState.Failed, "No service"), RecipientEntity("m1", "+1", ProcessingState.Sent, null)),
            listOf(StateEntity("m1", ProcessingState.Pending, 1_000), StateEntity("m1", ProcessingState.Sent, 2_000)),
        )
        val patch = CloudMapping.toPatch(stored)
        assertEquals("Sent", patch.state)
        assertEquals(listOf("+1", "+2"), patch.recipients.map { it.phoneNumber })
        assertEquals("No service", patch.recipients[1].error)
        assertEquals(setOf("Pending", "Sent"), patch.states.keys)
        assertEquals(Iso.format(2_000), patch.states["Sent"])
    }

    @Test fun `failed and cancelled reports cover every recipient`() {
        val m = message { it.copy(phoneNumbers = listOf("1", "2")) }
        val failed = CloudMapping.failedPatch(m, "nope", now)
        assertEquals("Failed", failed.state)
        assertEquals(listOf("nope", "nope"), failed.recipients.map { it.error })
        assertEquals(listOf("Cancelled", "Cancelled"), CloudMapping.cancelledPatch(m, now).recipients.map { it.state })
    }

    @Test fun `server json with unknown fields still parses`() {
        val json = """[{"id":"x","textMessage":{"text":"yo"},"phoneNumbers":["+1"],"state":"Pending","extra":true,"priority":0}]"""
        val parsed = ApiJson.decodeFromString<List<CloudMessage>>(json)
        assertEquals("yo", parsed.single().textMessage?.text)
        assertEquals("Pending", parsed.single().state)
    }

    @Test fun `retry delay grows and is capped`() {
        assertEquals(15_000L, CloudService.retryDelay(1))
        assertEquals(30_000L, CloudService.retryDelay(2))
        assertEquals(15 * 60_000L, CloudService.retryDelay(30))
    }
}

class CloudAuthChooserTest {
    private fun choose(
        existing: Boolean = false, login: String = "", password: String = "", privateServer: Boolean = true,
        key: String = "", savedLogin: String? = null, savedPassword: String? = null,
    ) = CloudAuthChooser.choose(existing, login, password, privateServer, key, savedLogin, savedPassword)

    @Test fun `first registration on a private server uses the server key`() =
        assertEquals(CloudAuth.ServerKey("k"), choose(key = " k "))

    @Test fun `first registration on the public server needs nothing`() =
        assertEquals(CloudAuth.None, choose(privateServer = false))

    @Test fun `joining an existing account uses its login`() =
        assertEquals(CloudAuth.Account("me", "pw"), choose(existing = true, login = " me ", password = "pw"))

    @Test fun `a phone that already has an account re-registers under it, not as a new one`() {
        assertEquals(
            CloudAuth.Account("ACCT01", "secret"),
            choose(key = "server-key", savedLogin = "ACCT01", savedPassword = "secret"),
        )
    }

    @Test fun `missing input is reported`() {
        assertThrows(IllegalStateException::class.java) { choose(key = "") }
        assertThrows(IllegalStateException::class.java) { choose(existing = true, login = "", password = "x") }
        assertThrows(IllegalStateException::class.java) { choose(existing = true, login = "me", password = "") }
    }
}
