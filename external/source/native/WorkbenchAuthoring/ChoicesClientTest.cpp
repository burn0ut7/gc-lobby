#include "ChoicesClient.h"
#include <QCoreApplication>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include "ScriptBridge.h"

#include <QTimer>

#include <cstdio>
#include <memory>

static void require(bool condition, const char *name)
{
    if (!condition) { std::fprintf(stderr, "FAIL %s\n", name); std::exit(1); }
    std::printf("PASS %s\n", name);
}

static std::function<QByteArray(const QByteArray &, QString &)> responder;
namespace gc {
QByteArray dispatchChoices(const QByteArray &request, QString &error) { return responder(request, error); }
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QString build(64, QChar('A'));
    for (int scenario = 0; scenario < 24; ++scenario) {
        auto lifetime = std::make_unique<QObject>();
        QEventLoop loop;
        QTimer timeout;
        timeout.setSingleShot(true);
        QObject::connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);
        bool gotRequest = false;
        int dispatches = 0;
        responder = [&](const QByteArray &encoded, QString &) -> QByteArray {
                ++dispatches;
                gotRequest = true;
                const auto request = QJsonDocument::fromJson(encoded).object();
                if (scenario < 11)
                    require(request.value("handler") == "GC_EditorChoices" &&
                        request.value("provider") == "TEST_Choices", "Calls the existing script choice owner");
                QJsonObject reply{{"adapterBuild", scenario == 1 ? QString(64, QChar('B')) : build},
                    {"token", request.value("token")}, {"state", scenario == 2 ? "provider_unavailable" : "ready"},
                    {"world", "$test:world.ent"}, {"values", QJsonArray{"USSR", "US", "US", QString::fromUtf8("Français")}}};
                if (scenario >= 5 && scenario < 11) {
                    require(request.value("kind") == "entities", "Explicit entity identity request");
                    reply.insert("kind", "entities");
                    reply.insert("entityIds", QJsonArray{"opaque-id-1", "opaque-id-2"});
                    reply.insert("entityNames", QJsonArray{"", ""});
                    reply.insert("entityLabels", QJsonArray{"Same label", "Same label"});
                    if (scenario == 6) reply.insert("entityNames", QJsonArray{""});
                    if (scenario == 7) reply.insert("entityIds", QJsonArray{"same-id", "same-id"});
                    if (scenario == 8) reply.remove("kind");
                    if (scenario == 9) reply.insert("entityLabels", QJsonArray{QString(QChar(0)), "Other"});
                    if (scenario == 10) {
                        reply.remove("entityIds");
                        reply.remove("entityNames");
                        reply.remove("entityLabels");
                    }
                }
                if (scenario >= 11) {
                    require(request.value("handler") == (scenario == 22 ? "GC_StringArrayEdit" : "GC_EntityReferenceCommit") &&
                        request.value("token") == "test-receipt" && request.value("adapterBuild") == build,
                        "Calls edit owner with original preparation receipt and validated build");
                    reply.insert("state", scenario == 11 || scenario == 19 ? "prepared" : "committed");
                    reply.insert("name", "GeneratedEntity1");
                    if (scenario >= 19 && scenario <= 22) {
                        require(request.value("operation") == (scenario == 22 ? "set_array_string" : "prepare_array") &&
                            request.value("expectedArray").toArray() == QJsonArray{" leading ", "a,b", "", QString::fromUtf8("Équipe"), ""},
                            "Array preparation preserves literal values and trailing empty entries");
                        if (scenario == 21) { reply.insert("state", "refused"); reply.insert("reason", "Array contents changed"); }
                    }
                    if (scenario == 14) reply.insert("token", "wrong-receipt");
                    if (scenario == 16) { reply.insert("state", "refused"); reply.insert("reason", "Destination changed"); }
                    if (scenario == 17) { reply.insert("state", "finalization_failed"); reply.insert("reason", "Inspect before retrying"); }
                    if (scenario == 23) require(request.value("operation") == "commit_array" &&
                        request.value("wireValue") == "@GC_ARRAY_test_APPLY" &&
                        request.value("rollbackValue") == "@GC_ARRAY_test_ROLLBACK", "Entity-array commit retains both native command tokens");
                }
                auto response = QJsonDocument(reply).toJson(QJsonDocument::Compact);
                if (scenario == 3) response = QByteArray(65537, 'x');
                if (scenario == 4 || scenario == 15) response = response.left(7);
                return response;
        };
        bool completed = false;
        gc::ChoicesReply observed;
        gc::EntityEditReply editObserved;
        if (scenario < 11) {
            gc::ChoicesClient::fetch(lifetime.get(), build, "TEST_Choices", [&](gc::ChoicesReply reply) {
                observed = std::move(reply); completed = true; loop.quit();
            }, scenario >= 5 ? gc::ChoiceKind::Entities : gc::ChoiceKind::Values);
        } else {
            gc::ChoicesClient::edit(lifetime.get(), build,
                {{"operation", scenario == 23 ? "commit_array" : (scenario == 22 ? "set_array_string" : (scenario >= 19 ? "prepare_array" : (scenario == 11 || scenario == 13 ? "prepare_scalar" : "commit_scalar")))},
                 {"wireValue", "@GC_ARRAY_test_APPLY"}, {"rollbackValue", "@GC_ARRAY_test_ROLLBACK"},
                 {"expectedArray", QJsonArray{" leading ", "a,b", "", QString::fromUtf8("Équipe"), ""}},
                 {"token", "test-receipt"}, {"handler", "must-be-replaced"}, {"adapterBuild", "must-be-replaced"}},
                [&](gc::EntityEditReply reply) {
                    editObserved = std::move(reply); completed = true;
                    if (scenario == 18) lifetime.reset();
                    loop.quit();
                });
        }
        timeout.start(3000);
        loop.exec();
        require(gotRequest && completed && dispatches == 1, "Deferred in-process request completed");
        if (scenario == 0) require(observed.state == "ready" && observed.world == "$test:world.ent" &&
            observed.values == QStringList{QString::fromUtf8("Français"), "US", "USSR"}, "UTF-8 response sorted and deduplicated");
        if (scenario == 0) require(observed.duplicateValues == QStringList{"US"},
            "Deduplication retains duplicate-source evidence");
        if (scenario == 1) require(observed.state == "unavailable" && observed.error.contains("identity"), "Reject wrong adapter build");
        if (scenario == 2) require(observed.state == "provider_unavailable", "Preserve unsupported provider state");
        if (scenario == 3) require(observed.state == "unavailable" && observed.error.contains("64 KiB"), "Reject oversized response");
        if (scenario == 4) require(observed.state == "unavailable", "Reject incomplete response");
        if (scenario == 5) require(observed.state == "ready" && observed.entities.size() == 2 &&
            observed.entities[0].id == "opaque-id-1" && observed.entities[1].id == "opaque-id-2" &&
            observed.entities[0].name.isEmpty() && observed.values.isEmpty(), "Keep distinct unnamed entities with identical labels");
        if (scenario >= 6 && scenario <= 9) require(observed.state == "unavailable" && observed.entities.isEmpty(),
            "Reject malformed entity metadata without partial choices");
        if (scenario == 10) require(observed.state == "ready" && observed.entities.isEmpty(),
            "Accept omitted empty entity arrays");
        if (scenario == 11) require(editObserved.state == "prepared", "Accept preparation response");
        if (scenario == 12) require(editObserved.state == "committed" && editObserved.name == "GeneratedEntity1", "Accept committed name");
        if (scenario == 13) require(editObserved.state == "unavailable" && editObserved.error.contains("phase"), "Reject mutation result for read-only preparation");
        if (scenario == 14) require(editObserved.state == "unavailable" && editObserved.error.contains("receipt"), "Reject edit receipt mismatch");
        if (scenario == 15) require(editObserved.state == "unavailable", "Incomplete edit reply remains uncertain without retry");
        if (scenario == 16) require(editObserved.state == "refused" && editObserved.reason == "Destination changed", "Preserve refusal reason");
        if (scenario == 17) require(editObserved.state == "finalization_failed" && editObserved.reason == "Inspect before retrying", "Preserve uncertain finalization outcome");
        if (scenario == 18) require(!lifetime && editObserved.state == "committed", "Completion may destroy request lifetime");
        if (scenario == 19) require(editObserved.state == "prepared" && editObserved.name == "GeneratedEntity1", "Array preparation returns proposed name without commit");
        if (scenario == 20) require(editObserved.state == "unavailable" && editObserved.error.contains("phase"), "Reject mutation result for array preparation");
        if (scenario == 21) require(editObserved.state == "refused" && editObserved.reason == "Array contents changed", "Preserve array preparation refusal");
        if (scenario == 22) require(editObserved.state == "committed", "Direct string-array edit reaches its owning handler");
        if (scenario == 23) require(editObserved.state == "committed" && editObserved.name == "GeneratedEntity1", "Entity-array commit reaches identity owner once");
    }
    {
        QObject lifetime;
        bool dispatched = false;
        responder = [&](const QByteArray &, QString &) { dispatched = true; return QByteArray{}; };
        bool completed = false;
        auto *request = gc::ChoicesClient::edit(&lifetime, build,
            {{"operation", "prepare_scalar"}, {"token", "cancel-before-dispatch"}},
            [&](gc::EntityEditReply) { completed = true; });
        delete request;
        QCoreApplication::processEvents();
        require(!completed && !dispatched, "Cancellation before dispatch sends no edit and invokes no callback");
    }
    for (int scenario = 0; scenario < 4; ++scenario) {
        QObject lifetime;
        QEventLoop loop;
        gc::ChoicesReply observed;
        bool completed = false;
        responder = [&](const QByteArray &encoded, QString &) {
            const auto request = QJsonDocument::fromJson(encoded).object();
            require(request.value("kind") == "context" && request.value("handler") == "GC_EditorChoices",
                "Initialization explicitly requests context without choices");
            QJsonObject reply{{"adapterBuild", build}, {"token", request.value("token")},
                {"state", "ready"}, {"kind", "context"}, {"world", "$test:world.ent"}};
            if (scenario == 1) reply.remove("kind");
            if (scenario == 2) reply.insert("world", "");
            if (scenario == 3) reply.insert("state", "world_unavailable");
            return QJsonDocument(reply).toJson(QJsonDocument::Compact);
        };
        gc::ChoicesClient::fetch(&lifetime, build, "TEST_Choices", [&](gc::ChoicesReply reply) {
            completed = true; observed = std::move(reply); loop.quit();
        }, gc::ChoiceKind::Context);
        QTimer::singleShot(3000, &loop, &QEventLoop::quit);
        loop.exec();
        require(completed, "Context request completes");
        if (scenario == 0) require(observed.state == "ready" && observed.world == "$test:world.ent" &&
            observed.values.isEmpty() && observed.entities.isEmpty(), "Context retains commit world without populating choices");
        if (scenario == 1 || scenario == 2) require(observed.state == "unavailable", "Reject incomplete context identity");
        if (scenario == 3) require(observed.state == "world_unavailable", "Unavailable world does not acquire commit context");
    }
    return 0;
}
