package app.messagegateway.sms.ui

import app.messagegateway.sms.data.WebhookEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebhookGroupsTest {
    private val url = "https://gateway.example.com/api/smsgate/webhook/AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    private fun hook(id: String, event: String, u: String = url, source: String = "Cloud") = WebhookEntity(id, u, event, source)

    @Test fun `one address with four events becomes one group in message order`() {
        val groups = WebhookGroups.group(
            listOf(hook("1", "sms:failed"), hook("2", "sms:received"), hook("3", "sms:delivered"), hook("4", "sms:sent"))
        )
        assertEquals(1, groups.size)
        assertEquals(listOf("Received", "Sent", "Delivered", "Failed"), groups.single().events.map { it.label })
        assertEquals(listOf("1", "2", "3", "4"), groups.single().ids)
    }

    @Test fun `the same event twice for one address is flagged`() {
        val group = WebhookGroups.group(listOf(hook("1", "sms:received"), hook("2", "sms:received"), hook("3", "sms:sent"))).single()
        assertEquals(2, group.events.first { it.event == "sms:received" }.count)
        assertTrue(group.hasDuplicates)
        assertFalse(WebhookGroups.group(listOf(hook("1", "sms:received"))).single().hasDuplicates)
    }

    @Test fun `different addresses stay separate and server ones come first`() {
        val groups = WebhookGroups.group(
            listOf(hook("1", "sms:received", "https://zzz.example/h", source = "Local"), hook("2", "sms:received"))
        )
        assertEquals(listOf("gateway.example.com", "zzz.example"), groups.map { it.host })
        assertTrue(groups[0].fromServer)
        assertFalse(groups[1].fromServer)
    }

    @Test fun `the same address from the server and from this phone are not merged`() {
        assertEquals(2, WebhookGroups.group(listOf(hook("1", "sms:received"), hook("2", "sms:received", source = "Local"))).size)
    }

    @Test fun `the secret in the address is hidden but the destination stays readable`() {
        val masked = WebhookGroups.mask(url)
        assertEquals("https://gateway.example.com/api/smsgate/webhook/••••••", masked)
        assertFalse(masked.contains("AbCdEfGh"))
    }

    @Test fun `short path words and ports survive, query strings are dropped`() {
        assertEquals("http://10.0.0.5:8765/hook", WebhookGroups.mask("http://10.0.0.5:8765/hook"))
        assertEquals("https://h.example/hook?…", WebhookGroups.mask("https://h.example/hook?token=abcdefghijklmnopqrstuvwxyz"))
    }

    @Test fun `unknown events get a readable label`() {
        assertEquals("Data received", WebhookGroups.label("sms:data-received"))
        assertEquals("Received", WebhookGroups.label("sms:received"))
    }
}
