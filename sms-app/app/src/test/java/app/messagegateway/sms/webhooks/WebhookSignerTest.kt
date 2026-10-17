package app.messagegateway.sms.webhooks

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WebhookSignerTest {
    @Test fun `signature matches an independently computed HMAC`() {
        // Computed with Python's hmac module: hmac(b"secret", body + timestamp, sha256).
        assertEquals(
            "5bfb71b792ec829c706a4d9bf7e6cb063d5abc4336963b9ecdb01288e5befb5b",
            WebhookSigner.sign("secret", "{\"a\":1}", "1700000000"),
        )
    }

    @Test fun `timestamp is part of the signed data`() {
        assertTrue(WebhookSigner.sign("k", "body", "1") != WebhookSigner.sign("k", "body", "2"))
    }

    @Test fun `retry delays grow and are capped at an hour`() {
        assertEquals(10_000L, WebhookService.backoff(1))
        assertEquals(20_000L, WebhookService.backoff(2))
        assertEquals(40_000L, WebhookService.backoff(3))
        assertEquals(3_600_000L, WebhookService.backoff(12))
        assertEquals(3_600_000L, WebhookService.backoff(40))
    }
}

class WebhookRedactTest {
    @Test fun `a webhook address is reduced to where it goes`() {
        assertEquals(
            "https://gateway.example.com/…",
            WebhookService.redact("https://gateway.example.com/api/smsgate/webhook/SECRETSECRET"),
        )
    }

    @Test fun `the secret never survives redaction`() {
        assertTrue(!WebhookService.redact("https://h.example/hook/topsecret?token=abc").contains("topsecret"))
        assertTrue(!WebhookService.redact("https://h.example/hook/topsecret?token=abc").contains("abc"))
    }

    @Test fun `unparseable addresses are hidden entirely`() = assertEquals("(address hidden)", WebhookService.redact("http://[bad"))
}
