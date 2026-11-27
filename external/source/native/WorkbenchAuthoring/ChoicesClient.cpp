#include "ChoicesClient.h"
#include "ScriptBridge.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QRegularExpression>
#include <QSet>
#include <QStringConverter>
#include <QTimer>
#include <QUuid>

namespace gc::detail {
class ChoicesRequest final : public QObject
{
public:
    ChoicesRequest(QObject *parent, QString build, QString selectedProvider,
        ChoicesClient::Completed callback, ChoiceKind selectedKind,
        QJsonObject edit = {}, ChoicesClient::EditCompleted editCallback = {})
        : QObject(parent), expectedBuild(std::move(build)), provider(std::move(selectedProvider)), kind(selectedKind),
          completed(std::move(callback)), editRequest(std::move(edit)), editCompleted(std::move(editCallback))
    {
        if (editCompleted) receipt = editRequest.value("token").toString();
        QTimer::singleShot(0, this, [this] { start(); });
    }

private:
    QString expectedBuild, provider;
    ChoiceKind kind;
    QString receipt = QUuid::createUuid().toString(QUuid::Id128);
    ChoicesClient::Completed completed;
    QJsonObject editRequest;
    ChoicesClient::EditCompleted editCompleted;
    bool finished = false;

    void start()
    {
        static const QRegularExpression identifier(QStringLiteral("^[A-Za-z_][A-Za-z0-9_]{0,95}$"));
        static const QRegularExpression hash(QStringLiteral("^[A-Fa-f0-9]{64}$"));
        if ((!editCompleted && !identifier.match(provider).hasMatch()) || !hash.match(expectedBuild).hasMatch()) {
            fail("Invalid provider or adapter build"); return;
        }
        if (editCompleted) {
            const auto operation = editRequest.value("operation").toString();
            static const QRegularExpression token(QStringLiteral("^[A-Za-z0-9_-]{1,64}$"));
            if ((operation != "prepare_scalar" && operation != "prepare_array" && operation != "commit_scalar" && operation != "commit_array" && operation != "set_array_string") ||
                !token.match(receipt).hasMatch() || QJsonDocument(editRequest).toJson(QJsonDocument::Compact).size() > 65536) {
                fail("Invalid entity edit operation, token or request size"); return;
            }
        }
        QJsonObject request{{"handler", "GC_EditorChoices"}, {"provider", provider}, {"token", receipt}};
        if (kind == ChoiceKind::Entities) request.insert("kind", "entities");
        if (kind == ChoiceKind::Context) request.insert("kind", "context");
        if (editCompleted) {
            request = editRequest;
            request.insert("handler", editRequest.value("operation") == "set_array_string" ? "GC_StringArrayEdit" : "GC_EntityReferenceCommit");
            request.insert("adapterBuild", expectedBuild);
        }
        const auto encoded = QJsonDocument(request).toJson(QJsonDocument::Compact);
        if (encoded.size() > 65536) { fail("Request exceeds 64 KiB"); return; }
        QString error;
        const QPointer<ChoicesRequest> guard(this);
        const auto response = dispatchChoices(encoded, error);
        if (!guard) return;
        if (!error.isEmpty()) { fail(error); return; }
        receive(response);
    }
    void fail(const QString &error)
    {
        if (editCompleted) {
            finishEdit({QStringLiteral("unavailable"), {}, {}, error});
            return;
        }
        finish({"unavailable", {}, {}, error, {}, {}});
    }
    void finishEdit(EntityEditReply reply)
    {
        if (finished) return;
        finished = true;
        auto callback = std::move(editCompleted);
        deleteLater();
        if (callback) callback(std::move(reply));
    }
    void finish(ChoicesReply reply)
    {
        if (finished) return;
        finished = true;
        auto callback = std::move(completed);
        deleteLater();
        if (callback) callback(std::move(reply));
    }
    void receive(const QByteArray &response)
    {
        if (finished) return;
        if (response.size() > 65536) { fail("Choice response exceeds 64 KiB"); return; }
        QStringDecoder decoder(QStringDecoder::Utf8);
        const QString decoded = decoder.decode(response);
        if (decoder.hasError()) { fail("Choice reply contains invalid UTF-8"); return; }
        QJsonParseError parseError;
        const auto document = QJsonDocument::fromJson(decoded.toUtf8(), &parseError);
        const auto body = document.object();
        if (parseError.error != QJsonParseError::NoError || !document.isObject() ||
            body.value("adapterBuild").toString() != expectedBuild || body.value("token").toString() != receipt) {
            fail("Choice adapter identity or receipt mismatch"); return;
        }
        const auto state = body.value("state").toString();
        if (editCompleted) {
            const QStringList states{"prepared", "refused", "committed", "unchanged", "rolled_back", "rollback_failed", "finalization_failed"};
            if (!states.contains(state) ||
                (body.contains("name") && !body.value("name").isString()) ||
                (body.contains("reason") && !body.value("reason").isString()) ||
                body.value("name").toString().contains(QChar(0)) || body.value("reason").toString().contains(QChar(0))) {
                fail("Malformed entity edit reply; inspect before retrying"); return;
            }
            const auto operation = editRequest.value("operation").toString();
            const bool preparing = operation == "prepare_scalar" || operation == "prepare_array";
            if ((preparing && state != "prepared" && state != "refused") || (!preparing && state == "prepared")) {
                fail("Entity edit phase mismatch; inspect before retrying"); return;
            }
            finishEdit({state, body.value("name").toString(), body.value("reason").toString(), {}});
            return;
        }
        if (state == "world_unavailable" || state == "provider_unavailable") { finish({state, {}, {}, {}, {}, {}}); return; }
        if (state != "ready" || !body.value("world").isString() ||
            (body.contains("values") && !body.value("values").isArray())) { fail("Malformed choice reply"); return; }
        if (kind == ChoiceKind::Context) {
            if (body.value("kind").toString() != "context" || body.value("world").toString().isEmpty()) {
                fail("Malformed editor context reply"); return;
            }
            finish({state, body.value("world").toString(), {}, {}, {}, {}});
            return;
        }
        if (kind == ChoiceKind::Entities) {
            if (body.value("kind").toString() != "entities") { fail("Entity choice kind mismatch"); return; }
            for (const auto &key : {"entityIds", "entityNames", "entityLabels"}) {
                if (body.contains(key) && !body.value(key).isArray()) { fail("Malformed entity choices"); return; }
            }
            const auto ids = body.value("entityIds").toArray();
            const auto names = body.value("entityNames").toArray();
            const auto labels = body.value("entityLabels").toArray();
            if (ids.size() != names.size() || ids.size() != labels.size()) { fail("Entity choice lengths differ"); return; }
            QVector<EntityChoice> entities;
            QSet<QString> seenIds;
            for (qsizetype i = 0; i < ids.size(); ++i) {
                if (!ids[i].isString() || !names[i].isString() || !labels[i].isString() ||
                    ids[i].toString().isEmpty() || ids[i].toString().contains(QChar(0)) ||
                    names[i].toString().contains(QChar(0)) || labels[i].toString().contains(QChar(0)) ||
                    seenIds.contains(ids[i].toString())) { fail("Invalid or duplicate entity identity"); return; }
                seenIds.insert(ids[i].toString());
                entities.append({ids[i].toString(), names[i].toString(), labels[i].toString()});
            }
            // IDs are opaque until the owning commit path revalidates them.
            // Never deduplicate by label/name: unnamed entities share those.
            finish({state, body.value("world").toString(), {}, {}, {}, entities});
            return;
        }
        QStringList values;
        QSet<QString> seen;
        QSet<QString> duplicates;
        // JsonApiStruct omits an empty registered array.
        for (const auto &entry : body.value("values").toArray()) {
            if (!entry.isString() || entry.toString().contains(QChar(0))) { fail("Invalid choice value"); return; }
            const auto value = entry.toString();
            if (!seen.contains(value)) { seen.insert(value); values.append(value); }
            else if (!value.isEmpty()) duplicates.insert(value);
        }
        values.sort(Qt::CaseSensitive);
        QStringList duplicateValues = duplicates.values();
        duplicateValues.sort(Qt::CaseSensitive);
        finish({state, body.value("world").toString(), values, {}, duplicateValues, {}});
    }
};
}

namespace gc {
QObject *ChoicesClient::fetch(QObject *lifetime, const QString &adapterBuild,
    const QString &provider, Completed completed, ChoiceKind kind)
{
    return new detail::ChoicesRequest(lifetime, adapterBuild, provider, std::move(completed), kind);
}
QObject *ChoicesClient::edit(QObject *lifetime, const QString &adapterBuild,
    QJsonObject request, EditCompleted completed)
{
    return new detail::ChoicesRequest(lifetime, adapterBuild, {}, {}, ChoiceKind::Values,
        std::move(request), std::move(completed));
}
}
