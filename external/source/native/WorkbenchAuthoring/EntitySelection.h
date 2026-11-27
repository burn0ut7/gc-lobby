#pragma once
#include "PropertySource.h"
#include "ArraySelection.h"
#include <QComboBox>
#include <QJsonObject>
#include <QPointer>
#include <functional>

namespace gc {
class EntitySelection final : public QObject
{
public:
    EntitySelection(QObject *property, QObject *editor, QComboBox *combo, QString provider,
        QString build, std::function<void(QJsonObject)> log);
    bool ready() const { return enabled; }
    void setWorld(QString value) { world = std::move(value); if (literalArray) literalArray->setWorld(world); }
    bool isBusy() const { return busy || (literalArray && literalArray->isBusy()); }
private:
    QPointer<QObject> property, editor;
    QPointer<QComboBox> combo;
    QPointer<ArraySelection> literalArray;
    QString provider, build, world, initialValue;
    std::function<void(QJsonObject)> log;
    bool enabled = false, busy = false;
    void activate(int index);
    void finishText();
    void restoreText(const QString &value);
    void finish(const QString &state, const QString &name, const QString &reason);
    void pick(int index);
};
}
