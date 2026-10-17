# kotlinx.serialization: keep generated serializers of our API models
-keepclassmembers class app.messagegateway.sms.** {
    *** Companion;
}
-keepclasseswithmembers class app.messagegateway.sms.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-dontwarn io.netty.**
-dontwarn org.slf4j.**
-dontwarn java.lang.management.**
