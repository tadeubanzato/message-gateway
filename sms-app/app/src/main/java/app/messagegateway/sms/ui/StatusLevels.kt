package app.messagegateway.sms.ui

import app.messagegateway.sms.cloud.CloudState
import app.messagegateway.sms.server.ServerState

/** How healthy something is, which decides whether it is drawn green, amber or red. */
enum class Level { Good, Warn, Bad }

object GatewayStatus {
    /** Switched off counts as red, like a connection that dropped: either way nothing is reaching the phone. */
    fun local(state: ServerState): Level = when (state) {
        is ServerState.Running -> Level.Good
        ServerState.Starting -> Level.Warn
        is ServerState.Failed, ServerState.Stopped -> Level.Bad
    }

    fun cloud(state: CloudState): Level = when (state) {
        is CloudState.Connected -> Level.Good
        CloudState.Connecting -> Level.Warn
        is CloudState.Failed, CloudState.Disabled -> Level.Bad
    }

    /**
     * The whole gateway: green when everything switched on works, red when nothing does (or nothing is on),
     * amber while something is still starting or only some of it works.
     */
    fun overall(local: ServerState, cloud: CloudState): Level {
        val switchedOn = buildList {
            if (local !is ServerState.Stopped) add(local(local))
            if (cloud !is CloudState.Disabled) add(cloud(cloud))
        }
        return when {
            switchedOn.isEmpty() -> Level.Bad
            switchedOn.all { it == Level.Good } -> Level.Good
            switchedOn.all { it == Level.Bad } -> Level.Bad
            else -> Level.Warn
        }
    }

    fun headline(local: ServerState, cloud: CloudState): String = when (overall(local, cloud)) {
        Level.Good -> "Online"
        Level.Bad -> "Offline"
        Level.Warn ->
            if (local(local) == Level.Bad && local !is ServerState.Stopped || cloud(cloud) == Level.Bad && cloud !is CloudState.Disabled) "Partly online"
            else if (local is ServerState.Running || cloud is CloudState.Connected) "Partly online"
            else "Starting…"
    }
}
