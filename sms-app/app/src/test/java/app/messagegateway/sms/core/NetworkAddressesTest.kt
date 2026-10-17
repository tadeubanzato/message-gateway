package app.messagegateway.sms.core

import app.messagegateway.sms.core.NetworkAddresses.Candidate
import app.messagegateway.sms.core.NetworkAddresses.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NetworkAddressesTest {
    @Test fun `wifi address is labelled and mobile data is left out`() {
        val picked = NetworkAddresses.pickLocal(
            listOf(
                Candidate(Kind.Cellular, listOf("100.72.14.9")),
                Candidate(Kind.Wifi, listOf("192.168.1.23")),
            )
        )
        assertEquals(listOf(LocalAddress("Wi-Fi", "192.168.1.23")), picked)
    }

    @Test fun `only mobile data means no local address`() {
        assertEquals(emptyList<LocalAddress>(), NetworkAddresses.pickLocal(listOf(Candidate(Kind.Cellular, listOf("100.72.14.9")))))
    }

    @Test fun `wired addresses are kept and labelled`() {
        assertEquals(
            listOf(LocalAddress("Ethernet", "10.0.0.5")),
            NetworkAddresses.pickLocal(listOf(Candidate(Kind.Ethernet, listOf("10.0.0.5")))),
        )
    }

    @Test fun `unusable addresses are dropped`() {
        val picked = NetworkAddresses.pickLocal(listOf(Candidate(Kind.Wifi, listOf("169.254.3.4", "0.0.0.0", "127.0.0.1", "192.168.0.7", "not-an-ip"))))
        assertEquals(listOf("192.168.0.7"), picked.map { it.ip })
    }

    @Test fun `the same address is listed once`() {
        val picked = NetworkAddresses.pickLocal(listOf(Candidate(Kind.Wifi, listOf("192.168.1.2", "192.168.1.2"))))
        assertEquals(1, picked.size)
    }

    @Test fun `ipv4 check`() {
        assertTrue(NetworkAddresses.isUsableIpv4("10.0.2.15"))
        assertFalse(NetworkAddresses.isUsableIpv4("256.1.1.1"))
        assertFalse(NetworkAddresses.isUsableIpv4("1.2.3"))
    }
}

class PublicIpParseTest {
    @Test fun `reads the address from the server reply`() =
        assertEquals("203.0.113.7", PublicIp.parse("""{"externalIp":"203.0.113.7","device":null}"""))

    @Test fun `accepts an ipv6 address`() =
        assertEquals("2001:db8::1", PublicIp.parse("""{"externalIp":"2001:db8::1"}"""))

    @Test fun `rejects junk and missing values`() {
        assertNull(PublicIp.parse("""{"externalIp":"<html>"}"""))
        assertNull(PublicIp.parse("""{"other":1}"""))
        assertNull(PublicIp.parse("not json"))
        assertNull(PublicIp.parse("""{"externalIp":""}"""))
    }
}
