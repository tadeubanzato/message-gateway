package app.messagegateway.sms.ui

import app.messagegateway.sms.data.OutgoingPoint
import app.messagegateway.sms.domain.ProcessingState
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/** One day of activity. */
data class DayBucket(val date: LocalDate, val sent: Int, val failed: Int, val received: Int) {
    val outgoing: Int get() = sent + failed
}

/** The maths behind the activity chart, kept free of drawing code so it can be tested. */
object Trends {
    /**
     * Counts per local calendar day for the last [days] days ending [today]. An outgoing text counts on the day it was
     * created; it is "sent" once the network took it (sent or delivered) and "failed" once it failed. Texts still on
     * their way and cancelled ones are not counted.
     */
    fun buckets(days: Int, today: LocalDate, zone: ZoneId, outgoing: List<OutgoingPoint>, received: List<Long>): List<DayBucket> {
        val first = today.minusDays(days - 1L)
        val sent = IntArray(days)
        val failed = IntArray(days)
        val inbound = IntArray(days)

        fun dayIndex(millis: Long): Int? {
            val day = Instant.ofEpochMilli(millis).atZone(zone).toLocalDate()
            return ChronoUnit.DAYS.between(first, day).toInt().takeIf { it in 0 until days }
        }
        for (p in outgoing) {
            val i = dayIndex(p.at) ?: continue
            when (p.state) {
                ProcessingState.Sent, ProcessingState.Delivered -> sent[i]++
                ProcessingState.Failed -> failed[i]++
                else -> Unit
            }
        }
        for (at in received) dayIndex(at)?.let { inbound[it]++ }
        return (0 until days).map { DayBucket(first.plusDays(it.toLong()), sent[it], failed[it], inbound[it]) }
    }

    /** Midnight of the first day shown, as epoch millis, so queries fetch only what the chart needs. */
    fun startMillis(days: Int, today: LocalDate, zone: ZoneId): Long =
        today.minusDays(days - 1L).atStartOfDay(zone).toInstant().toEpochMilli()

    private val steps = listOf(1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1_000, 2_000, 2_500, 5_000, 10_000, 20_000, 50_000, 100_000)

    /** Round tick values from 0 up past [max], at most four steps, so the axis reads in clean numbers. */
    fun niceTicks(max: Int): List<Int> {
        if (max <= 0) return listOf(0, 1)
        val step = steps.firstOrNull { (max + it - 1) / it <= 4 } ?: ((max + 3) / 4)
        val top = (max + step - 1) / step * step
        return (0..top step step).toList()
    }

    /** 950 -> "950", 1000 -> "1K", 1500 -> "1.5K". */
    fun compact(n: Int): String = when {
        n < 1_000 -> n.toString()
        n % 1_000 == 0 -> "${n / 1_000}K"
        else -> "%.1fK".format(n / 1_000.0)
    }

    /** Which days get an axis label: today always, then evenly back, so labels never crowd. */
    fun labelIndexes(days: Int): List<Int> {
        if (days <= 0) return emptyList()   // before the first data arrives there are no days to label
        val step = (days + 4) / 5
        return ((days - 1) downTo 0 step step).toList().reversed()
    }
}
