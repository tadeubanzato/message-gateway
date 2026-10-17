package app.messagegateway.sms.ui

import app.messagegateway.sms.cloud.CloudState
import app.messagegateway.sms.server.ServerState
import org.junit.Assert.assertEquals
import org.junit.Test

class GatewayStatusTest {
    private val running = ServerState.Running(8080)
    private val connected = CloudState.Connected(0)

    @Test fun `a connected mode is green and a dropped or switched off one is red`() {
        assertEquals(Level.Good, GatewayStatus.local(running))
        assertEquals(Level.Good, GatewayStatus.cloud(connected))
        assertEquals(Level.Bad, GatewayStatus.local(ServerState.Stopped))
        assertEquals(Level.Bad, GatewayStatus.cloud(CloudState.Disabled))
        assertEquals(Level.Bad, GatewayStatus.local(ServerState.Failed("port in use")))
        assertEquals(Level.Bad, GatewayStatus.cloud(CloudState.Failed("no route")))
    }

    @Test fun `starting and connecting are amber`() {
        assertEquals(Level.Warn, GatewayStatus.local(ServerState.Starting))
        assertEquals(Level.Warn, GatewayStatus.cloud(CloudState.Connecting))
    }

    @Test fun `everything on and working is green`() {
        assertEquals(Level.Good, GatewayStatus.overall(running, connected))
        assertEquals("Online", GatewayStatus.headline(running, connected))
    }

    @Test fun `one mode on and working is green even if the other is simply off`() {
        assertEquals(Level.Good, GatewayStatus.overall(running, CloudState.Disabled))
        assertEquals(Level.Good, GatewayStatus.overall(ServerState.Stopped, connected))
    }

    @Test fun `nothing on is red`() {
        assertEquals(Level.Bad, GatewayStatus.overall(ServerState.Stopped, CloudState.Disabled))
        assertEquals("Offline", GatewayStatus.headline(ServerState.Stopped, CloudState.Disabled))
    }

    @Test fun `everything on but broken is red`() {
        assertEquals(Level.Bad, GatewayStatus.overall(ServerState.Failed("x"), CloudState.Failed("y")))
        assertEquals(Level.Bad, GatewayStatus.overall(ServerState.Stopped, CloudState.Failed("y")))
        assertEquals("Offline", GatewayStatus.headline(ServerState.Stopped, CloudState.Failed("y")))
    }

    @Test fun `one working and one broken is amber and says partly online`() {
        assertEquals(Level.Warn, GatewayStatus.overall(running, CloudState.Failed("y")))
        assertEquals("Partly online", GatewayStatus.headline(running, CloudState.Failed("y")))
    }

    @Test fun `still starting is amber`() {
        assertEquals(Level.Warn, GatewayStatus.overall(ServerState.Starting, CloudState.Disabled))
        assertEquals("Starting…", GatewayStatus.headline(ServerState.Starting, CloudState.Disabled))
        assertEquals("Starting…", GatewayStatus.headline(ServerState.Stopped, CloudState.Connecting))
    }
}
