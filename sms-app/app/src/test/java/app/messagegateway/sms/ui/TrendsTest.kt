package app.messagegateway.sms.ui

import app.messagegateway.sms.data.OutgoingPoint
import app.messagegateway.sms.domain.ProcessingState
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

class TrendsTest {
    private val zone = ZoneId.of("America/Los_Angeles")
    private val today = LocalDate.of(2026, 10, 17)
    private fun at(day: LocalDate, hour: Int = 12) = day.atTime(hour, 0).atZone(zone).toInstant().toEpochMilli()

    @Test fun `a range has one bucket per day ending today`() {
        val buckets = Trends.buckets(7, today, zone, emptyList(), emptyList())
        assertEquals(7, buckets.size)
        assertEquals(LocalDate.of(2026, 10, 11), buckets.first().date)
        assertEquals(today, buckets.last().date)
    }

    @Test fun `texts are counted on their own day by how they ended`() {
        val points = listOf(
            OutgoingPoint(at(today), ProcessingState.Delivered),
            OutgoingPoint(at(today), ProcessingState.Sent),
            OutgoingPoint(at(today), ProcessingState.Failed),
            OutgoingPoint(at(today.minusDays(2)), ProcessingState.Delivered),
        )
        val b = Trends.buckets(7, today, zone, points, emptyList())
        assertEquals(DayBucket(today, 2, 1, 0), b.last())
        assertEquals(1, b.first { it.date == today.minusDays(2) }.sent)
    }

    @Test fun `texts still on their way and cancelled ones are not counted`() {
        val points = listOf(ProcessingState.Pending, ProcessingState.Processed, ProcessingState.Cancelled).map { OutgoingPoint(at(today), it) }
        assertEquals(0, Trends.buckets(7, today, zone, points, emptyList()).last().outgoing)
    }

    @Test fun `received texts get their own count`() {
        val b = Trends.buckets(7, today, zone, emptyList(), listOf(at(today), at(today), at(today.minusDays(1))))
        assertEquals(2, b.last().received)
        assertEquals(1, b[b.size - 2].received)
    }

    @Test fun `days are the viewer's local days, not UTC`() {
        // 11 pm in Los Angeles is already the next day in UTC; it must still count as the local evening.
        val lateEvening = today.atTime(23, 0).atZone(zone).toInstant().toEpochMilli()
        assertEquals(LocalDate.of(2026, 10, 18), java.time.Instant.ofEpochMilli(lateEvening).atZone(ZoneOffset.UTC).toLocalDate())
        assertEquals(1, Trends.buckets(7, today, zone, listOf(OutgoingPoint(lateEvening, ProcessingState.Sent)), emptyList()).last().sent)
    }

    @Test fun `anything outside the range is ignored`() {
        val old = OutgoingPoint(at(today.minusDays(40)), ProcessingState.Sent)
        val future = OutgoingPoint(at(today.plusDays(2)), ProcessingState.Sent)
        assertEquals(0, Trends.buckets(30, today, zone, listOf(old, future), emptyList()).sumOf { it.sent })
    }

    @Test fun `the query starts at midnight of the first day`() {
        assertEquals(today.minusDays(6).atStartOfDay(zone).toInstant().toEpochMilli(), Trends.startMillis(7, today, zone))
    }

    @Test fun `axis ticks are round numbers that cover the data in at most four steps`() {
        assertEquals(listOf(0, 1), Trends.niceTicks(0))
        assertEquals(listOf(0, 1), Trends.niceTicks(1))
        assertEquals(listOf(0, 2, 4, 6), Trends.niceTicks(5))
        assertEquals(listOf(0, 5, 10), Trends.niceTicks(10))
        assertEquals(listOf(0, 10, 20, 30), Trends.niceTicks(23))
        assertEquals(listOf(0, 250, 500, 750, 1000), Trends.niceTicks(900))
    }

    @Test fun `ticks always reach the largest value`() {
        for (max in 1..5000 step 37) assert(Trends.niceTicks(max).last() >= max) { "max $max" }
    }

    @Test fun `big numbers are compact`() {
        assertEquals("950", Trends.compact(950))
        assertEquals("1K", Trends.compact(1000))
        assertEquals("2K", Trends.compact(2000))
    }

    @Test fun `before any data arrives there is nothing to label and nothing crashes`() {
        assertEquals(emptyList<Int>(), Trends.labelIndexes(0))
        assertEquals(emptyList<Int>(), Trends.labelIndexes(-1))
        assertEquals(emptyList<DayBucket>(), Trends.buckets(0, today, zone, emptyList(), emptyList()))
    }

    @Test fun `very short ranges still label their last day`() {
        for (days in 1..6) assertEquals(days - 1, Trends.labelIndexes(days).last())
    }

    @Test fun `axis labels always include today and never crowd`() {
        assertEquals(listOf(0, 2, 4, 6), Trends.labelIndexes(7))
        assertEquals(29, Trends.labelIndexes(30).last())
        for (days in listOf(7, 14, 30)) assert(Trends.labelIndexes(days).size in 4..6) { "days $days" }
    }
}
