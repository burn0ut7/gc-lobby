#include "ScriptBridge.h"
#include "Compatibility.h"
#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

namespace gc {
namespace {
// Workbench 1.9.0.42: the engine's own Managed invocation and JsonApiStruct
// serialization ABI. JsonApiStruct is a serializer; no NET API is initialized.
// Resolve objects anew per request so script reload cannot leave stale pointers.
template<class T> T function(quintptr base, quintptr rva) { return reinterpret_cast<T>(base + rva); }
class ManagedRef {
public:
    explicit ManagedRef(quintptr value, bool adopt = false) : value(value) {
        if (value && !adopt && InterlockedIncrement(reinterpret_cast<volatile LONG *>(value + 0x18)) == 1)
            reinterpret_cast<void (*)(quintptr)>(reinterpret_cast<quintptr *>(*reinterpret_cast<quintptr *>(value + 0x10))[2])(value + 0x10);
    }
    ~ManagedRef() {
        if (value && InterlockedDecrement(reinterpret_cast<volatile LONG *>(value + 0x18)) == 0)
            reinterpret_cast<void (*)(quintptr)>(reinterpret_cast<quintptr *>(*reinterpret_cast<quintptr *>(value + 0x10))[1])(value + 0x10);
    }
    ManagedRef(const ManagedRef &) = delete;
    ManagedRef &operator=(const ManagedRef &) = delete;
    quintptr value;
};
quintptr invoke(quintptr base, quintptr object, const char *method, quintptr argument = 0)
{
    quintptr result = 0;
    function<quintptr *(*)(quintptr, quintptr *, const char *, ...)>(base, 0x1ef6b80)(object, &result, method, argument);
    if (!result) return 0;
    const auto managed = function<quintptr (*)(quintptr, quintptr)>(base, 0x1ff0440)(result, result);
    // Transfer ownership from the temporary script variant to the caller.
    if (managed && InterlockedIncrement(reinterpret_cast<volatile LONG *>(managed + 0x18)) == 1)
        reinterpret_cast<void (*)(quintptr)>(reinterpret_cast<quintptr *>(*reinterpret_cast<quintptr *>(managed + 0x10))[2])(managed + 0x10);
    function<void (*)(quintptr *)>(base, 0x1fef4c0)(&result);
    return managed;
}
}
QByteArray dispatchChoices(const QByteArray &request, QString &error)
{
    auto *application = QCoreApplication::instance();
    if (!application || QThread::currentThread() != application->thread()) {
        error = QStringLiteral("Dropdown bridge requires the Workbench application thread"); return {};
    }
    static const QString hostError = incompatibility(inspectHost());
    if (!hostError.isEmpty()) { error = hostError; return {}; }
    const auto document = QJsonDocument::fromJson(request);
    const auto handler = document.object().value("handler").toString().toLatin1();
    if (request.size() > 65536 || !document.isObject() ||
        (handler != "GC_EditorChoices" && handler != "GC_StringArrayEdit" && handler != "GC_EntityReferenceCommit")) {
        error = QStringLiteral("Invalid dropdown bridge request"); return {};
    }
    const auto base = reinterpret_cast<quintptr>(GetModuleHandleW(nullptr));
    const auto context = *reinterpret_cast<quintptr *>(base + 0x3e8f658);
    if (!context) { error = QStringLiteral("Workbench scripts are unavailable"); return {}; }
    const auto type = function<quintptr (*)(quintptr, const char *, bool)>(base, 0x1eba9b0)(context, handler.constData(), false);
    if (!type) { error = QStringLiteral("GC dropdown handler is unavailable"); return {}; }
    ManagedRef owner(function<quintptr (*)(quintptr, bool)>(base, 0x1efb1d0)(type, true));
    if (!owner.value) { error = QStringLiteral("Cannot create GC dropdown handler"); return {}; }
    const auto requestObject = invoke(base, owner.value, "GetRequest");
    ManagedRef input(requestObject, true);
    if (!input.value) { error = QStringLiteral("GC dropdown request is unavailable"); return {}; }
    function<void (*)(quintptr, const char *)>(base, 0x1b734c0)(input.value, request.constData());
    const auto responseObject = invoke(base, owner.value, "GetResponse", input.value);
    ManagedRef output(responseObject, true);
    if (!output.value) { error = QStringLiteral("GC dropdown handler returned no response; inspect before retrying"); return {}; }
    function<void (*)(quintptr)>(base, 0x1b7a110)(output.value);
    const auto json = function<const char *(*)(quintptr)>(base, 0x1b6ec20)(output.value);
    const auto length = json ? strnlen_s(json, 65537) : 0;
    if (!length || length > 65536) { error = QStringLiteral("Dropdown response is empty or exceeds 64 KiB"); return {}; }
    return QByteArray(json, qsizetype(length));
}
}
