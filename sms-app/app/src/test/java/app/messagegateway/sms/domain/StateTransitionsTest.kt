package app.messagegateway.sms.domain

import app.messagegateway.sms.domain.ProcessingState.*
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StateTransitionsTest {
    @Test fun `normal path is allowed`() {
        assertTrue(StateTransitions.canTransition(Pending, Processed))
        assertTrue(StateTransitions.canTransition(Processed, Sent))
        assertTrue(StateTransitions.canTransition(Sent, Delivered))
    }

    @Test fun `delivery report can arrive before the sent callback`() {
        assertTrue(StateTransitions.canTransition(Processed, Delivered))
    }

    @Test fun `failed is terminal`() {
        ProcessingState.entries.filter { it != Failed }.forEach {
            assertFalse("Failed -> $it", StateTransitions.canTransition(Failed, it))
        }
    }

    @Test fun `a cancel that came too late yields to what the radio did`() {
        assertTrue(StateTransitions.canTransition(Cancelled, Sent))
        assertTrue(StateTransitions.canTransition(Cancelled, Failed))
        assertFalse(StateTransitions.canTransition(Cancelled, Processed))
    }

    @Test fun `no going backwards`() {
        assertFalse(StateTransitions.canTransition(Sent, Pending))
        assertFalse(StateTransitions.canTransition(Delivered, Sent))
    }

    @Test fun `same state is a no-op`() {
        ProcessingState.entries.forEach { assertTrue(StateTransitions.canTransition(it, it)) }
    }

    @Test fun `aggregate waits for pending recipients`() {
        assertEquals(Pending, StateTransitions.aggregate(listOf(Sent, Pending)))
        assertEquals(Processed, StateTransitions.aggregate(listOf(Sent, Processed)))
    }

    @Test fun `aggregate of finished recipients`() {
        assertEquals(Delivered, StateTransitions.aggregate(listOf(Delivered, Delivered)))
        assertEquals(Sent, StateTransitions.aggregate(listOf(Delivered, Sent)))
        assertEquals(Failed, StateTransitions.aggregate(listOf(Failed, Failed)))
        assertEquals(Cancelled, StateTransitions.aggregate(listOf(Cancelled, Cancelled)))
    }

    @Test fun `a failed recipient does not hide the others`() {
        assertEquals(Delivered, StateTransitions.aggregate(listOf(Delivered, Failed)))
        assertEquals(Sent, StateTransitions.aggregate(listOf(Sent, Failed)))
    }

    @Test fun `empty is pending`() = assertEquals(Pending, StateTransitions.aggregate(emptyList()))
}
